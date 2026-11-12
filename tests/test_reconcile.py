import re

import pandas as pd
import pytest
from conftest import ROOT

from playbook import legacy, reconcile, sample_data


@pytest.fixture(scope="module")
def data():
    return sample_data.generate(seed=11, customers=120, lines=3000)


@pytest.fixture(scope="module")
def sides(data):
    return reconcile.run_legacy(data), reconcile.run_new(data)


def test_example_reconciles_with_only_accepted_differences(sides):
    old, new = sides
    diffs = reconcile.compare(old, new)
    reconcile.classify(diffs, reconcile.load_accepted())
    assert diffs, "the planted lookup differences should show up"
    assert all(d.accepted_by == "code_case_or_trailing_space" for d in diffs)


def test_row_counts_agree(sides):
    old, new = sides
    for t in reconcile.TABLES:
        assert len(old[t]) == len(new[t]), t


def test_unaccepted_difference_is_reported(sides):
    old, new = sides
    broken = dict(new)
    f = new["fact_sales"].copy()
    f.loc[f.index[0], "net_amount"] += 1
    broken["fact_sales"] = f
    diffs = reconcile.compare(old, broken)
    reconcile.classify(diffs, reconcile.load_accepted())
    left = [d for d in diffs if d.accepted_by is None]
    assert len(left) == 1 and left[0].column == "net_amount"
    assert "Unexpected differences: 1" in reconcile.report(old, broken, diffs, reconcile.load_accepted())


def test_missing_row_is_reported(sides):
    old, new = sides
    broken = dict(new)
    broken["sales_rejected"] = new["sales_rejected"].iloc[1:]
    diffs = reconcile.compare(old, broken)
    assert [d.kind for d in diffs if d.table == "sales_rejected" and d.kind != "column"] == ["only_legacy"]


def test_unknown_matcher_is_rejected(tmp_path):
    p = tmp_path / "a.yml"
    p.write_text("accepted:\n  - {id: x, matcher: nope, tables: [fact_sales], adr: x}\n")
    with pytest.raises(ValueError, match="unknown matcher"):
        reconcile.load_accepted(p)


def test_type1_change_reaches_every_version(sides, data):
    old, new = sides
    changed = data["stg_customer"].merge(
        data["dim_customer_before"].query("is_current == 1"), on="customer_code", suffixes=("", "_dim")
    )
    changed = changed[changed["customer_name"] != changed["customer_name_dim"]]
    assert len(changed)
    for side in (old, new):
        dim = side["dim_customer"]
        for code, name in zip(changed["customer_code"], changed["customer_name"], strict=True):
            assert set(dim.loc[dim["customer_code"] == code, "customer_name"]) == {name}


def test_legacy_lookup_is_exact():
    dim = pd.DataFrame({"customer_key": [1], "customer_code": ["C00001"], "is_current": [1]})
    prod = pd.DataFrame({"product_key": [1], "product_code": ["P0001"]})
    lines = pd.DataFrame(
        {
            "order_id": [1, 1, 1],
            "line_number": [1, 2, 3],
            "customer_code": ["C00001", "c00001", "C00001 "],
            "product_code": ["P0001"] * 3,
            "order_date": ["2026-03-01"] * 3,
            "quantity": [1, 1, 1],
            "unit_price": [1.0] * 3,
        }
    )
    fact, _ = legacy.fact_sales(lines, dim, prod)
    assert fact["customer_key"].tolist() == [1, -1, -1]


def _statements(path):
    sql = "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--"))
    return [
        re.match(r"\s*(\w+(?:\s+\w+)?)", s).group(1).upper()
        for s in sql.split(";")
        if s.strip()
        and not s.strip().upper().startswith(("GO", "SET", "BEGIN", "COMMIT", "END", "DECLARE", "CREATE OR ALTER"))
    ]


def test_reference_sql_follows_the_procedure():
    """The DuckDB copy must keep the procedure's statements in the same order."""
    proc = _statements(ROOT / "sql" / "procedures" / "usp_load_dim_customer.sql")
    ref = _statements(ROOT / "sql" / "reference" / "load_dim_customer.sql")
    assert [s.split()[0] for s in proc] == [s.split()[0] for s in ref] == ["UPDATE", "UPDATE", "INSERT"]


def test_reference_sql_keeps_the_type1_rule():
    for f in ("sql/procedures/usp_load_dim_customer.sql", "sql/reference/load_dim_customer.sql"):
        text = "\n".join(ln for ln in (ROOT / f).read_text().splitlines() if not ln.lstrip().startswith("--"))
        first_update = text.split("UPDATE", 1)[1].split(";", 1)[0]
        assert "is_current" not in first_update, f
