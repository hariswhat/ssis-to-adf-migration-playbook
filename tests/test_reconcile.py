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
    """First keyword of each data statement, skipping session and control
    statements and the lock, which only the T-SQL has."""
    sql = "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--"))
    skip = ("GO", "SET ", "SET\n", "BEGIN", "COMMIT", "END", "DECLARE", "CREATE OR ALTER", "EXEC SP_GETAPPLOCK")
    out = []
    for s in sql.split(";"):
        s = s.strip()
        if not s or s.upper().startswith(skip):
            continue
        word = re.match(r"(\w+)", s).group(1).upper()
        out.append("CREATE" if word == "SELECT" and " INTO #" in s else word)
    return out


@pytest.mark.parametrize(
    "proc, ref, expected",
    [
        ("usp_load_dim_customer", "load_dim_customer", ["UPDATE", "UPDATE", "INSERT"]),
        ("usp_load_fact_sales", "load_fact_sales", ["CREATE", "DELETE", "DELETE", "INSERT", "INSERT", "UPDATE"]),
    ],
)
def test_reference_sql_follows_the_procedure(proc, ref, expected):
    """The DuckDB copy keeps the procedure's data statements in the same order."""
    assert _statements(ROOT / "sql" / "procedures" / f"{proc}.sql") == expected
    assert _statements(ROOT / "sql" / "reference" / f"{ref}.sql") == expected


def test_reference_sql_keeps_the_type1_rule():
    for f in ("sql/procedures/usp_load_dim_customer.sql", "sql/reference/load_dim_customer.sql"):
        text = "\n".join(ln for ln in (ROOT / f).read_text().splitlines() if not ln.lstrip().startswith("--"))
        first_update = text.split("UPDATE", 1)[1].split(";", 1)[0]
        assert "is_current" not in first_update, f


def test_new_sql_is_rerunnable(data):
    once, twice = reconcile.run_new(data), reconcile.run_new(data, runs=2)
    for t in reconcile.TABLES:
        cols = list(once[t].columns)
        a = once[t].sort_values(cols[:2]).reset_index(drop=True)
        b = twice[t].sort_values(cols[:2]).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_duplicate_and_dangling_keys_are_reported(sides):
    old, new = sides
    broken = dict(new)
    f = new["fact_sales"]
    broken["fact_sales"] = pd.concat([f, f.iloc[:2]], ignore_index=True)
    diffs = reconcile.compare(old, broken)
    assert sum(d.kind == "duplicate_new" for d in diffs) == 2
    f2 = new["fact_sales"].copy()
    f2.loc[f2.index[0], "customer_key"] = 999_999
    broken["fact_sales"] = f2
    diffs = reconcile.compare(old, broken)
    reconcile.classify(diffs, reconcile.load_accepted())
    assert any(str(d.new).startswith("dangling:") and d.accepted_by is None for d in diffs)


def test_every_column_and_small_amounts_are_compared(sides):
    old, new = sides
    broken = dict(new)
    f = new["fact_sales"].copy()
    f["order_date"] = f["order_date"].astype("datetime64[ns]")
    f.loc[f.index[0], "order_date"] = pd.Timestamp("2026-03-02")
    f.loc[f.index[1], "net_amount"] = float(f.loc[f.index[1], "net_amount"]) + 0.004
    broken["fact_sales"] = f
    diffs = [d for d in reconcile.compare(old, broken) if d.kind == "column" and d.column != "customer"]
    assert {d.column for d in diffs} == {"order_date", "net_amount"}


def test_load_time_matcher_is_narrow():
    def diff(col, a, b):
        return reconcile.Difference("dim_customer", ("C1", 2), "column", col, a, b)

    assert reconcile.MATCHERS["load_time"](diff("valid_from", "2026-03-02 01:30:00", "2026-03-02 01:41:12"))
    assert not reconcile.MATCHERS["load_time"](diff("valid_from", "2026-03-02 01:30:00", "2026-03-03 01:30:00"))
    assert not reconcile.MATCHERS["load_time"](diff("valid_to", "9999-12-31 00:00:00", "2026-03-02 01:30:00"))
    assert not reconcile.MATCHERS["load_time"](diff("region", "West", "East"))


def test_lowercase_staged_code_is_matched_like_sql_server(sides, data):
    lower = data["stg_customer"].loc[data["stg_customer"]["customer_code"].str.islower(), "customer_code"]
    assert len(lower)
    for side in sides:
        dim = side["dim_customer"]
        for code in lower:
            current = dim[(dim["customer_code"].str.upper() == code.upper()) & (dim["is_current"] == 1)]
            assert current["customer_code"].tolist() == [code]
