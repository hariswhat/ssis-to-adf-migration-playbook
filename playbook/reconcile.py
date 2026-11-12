"""Compare what the SSIS package wrote with what its replacement writes.

During the parallel run both platforms load from the same staging data into
separate schemas, and this compares the results table by table: rows only on
one side, and rows on both sides whose columns differ. Every difference is
then matched against reconciliation/accepted.yml, a list of behaviour changes
that were reviewed and agreed (each with the ADR that accepts it). Anything
left over fails the comparison.

Surrogate keys are assigned independently on each side, so a fact's
customer_key is compared through the dimension row it points at, not as a
number.

The example below runs both sides on generated data: the SSIS behaviour from
playbook/legacy.py and the new SQL from sql/reference (DuckDB copies of the
procedures in sql/procedures).

    python -m playbook.reconcile
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from . import legacy, sample_data

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = ROOT / "reconciliation" / "accepted.yml"
REFERENCE = ROOT / "sql" / "reference"

FACT_COLUMNS = ["customer_code", "customer", "product_key", "order_date", "quantity", "unit_price", "net_amount"]
TABLES = {
    # table: (natural key, compared columns). A dimension row is identified by
    # its code and version number, not its timestamps, which differ between
    # two platforms that load at different times.
    "dim_customer": (
        ["code", "version"],
        ["customer_code", "customer_name", "region", "tier", "valid_from", "valid_to", "is_current"],
    ),
    "fact_sales": (["order_id", "line_number"], FACT_COLUMNS),
    "sales_rejected": (["order_id", "line_number"], FACT_COLUMNS),
}
AMOUNT_TOLERANCE = 1e-9  # amounts compared exactly, up to float representation


@dataclass
class Difference:
    table: str
    key: tuple
    kind: str  # only_legacy, only_new, column
    column: str | None = None
    legacy: object = None
    new: object = None
    row: dict = field(default_factory=dict)
    accepted_by: str | None = None


# ---------------------------------------------------------------- run both sides


def run_legacy(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    dim = legacy.scd(data["dim_customer_before"], data["stg_customer"], sample_data.LOAD_TS)
    fact, rejected = legacy.fact_sales(data["stg_order_line"], dim, data["dim_product"])
    return {"dim_customer": dim, "fact_sales": fact, "sales_rejected": rejected}


def run_new(data: dict[str, pd.DataFrame], runs: int = 1) -> dict[str, pd.DataFrame]:
    """Run the reference SQL; runs=2 repeats the load on the same staging
    data, which must change nothing (ADR-0003)."""
    con = duckdb.connect()
    con.register("_dim", data["dim_customer_before"])
    con.execute("CREATE TABLE dim_customer AS SELECT * FROM _dim")
    con.execute("CREATE TABLE watermark (table_name VARCHAR, watermark_at TIMESTAMP)")
    con.execute("INSERT INTO watermark VALUES ('order_line', TIMESTAMP '1900-01-01')")
    for t in ("fact_sales", "sales_rejected"):
        con.execute(
            f"CREATE TABLE {t} (order_id BIGINT, line_number INTEGER, customer_code VARCHAR, customer_key BIGINT,"
            " product_key BIGINT, order_date DATE, quantity INTEGER, unit_price DOUBLE, net_amount DECIMAL(18, 2))"
        )
    for name in ("stg_customer", "dim_product", "stg_order_line"):
        con.register(f"_{name}", data[name])
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}")
    for f in ("load_dim_customer.sql", "load_fact_sales.sql") * runs:
        for stmt in _statements((REFERENCE / f).read_text()):
            con.execute(stmt, {"load_ts": sample_data.LOAD_TS} if "$load_ts" in stmt else None)
    return {t: con.execute(f"SELECT * FROM {t}").df() for t in TABLES}


def _statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


# ---------------------------------------------------------------- compare


def _versioned(dim: pd.DataFrame) -> pd.DataFrame:
    """Add (code, version): the code as the database compares it, and the
    version's position in that code's history."""
    out = dim.copy()
    out["code"] = out["customer_code"].map(legacy.db_key)
    out["_from"] = [_ts(v) for v in out["valid_from"]]
    out["version"] = out.sort_values(["code", "_from"]).groupby("code").cumcount() + 1
    return out.drop(columns="_from")


def _with_customer(fact: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    """Replace customer_key by the dimension version it points at. -1 is the
    unknown member; a key with no dimension row is reported as dangling."""
    v = _versioned(dim)
    ident = {k: f"{c}#{n}" for k, c, n in zip(v["customer_key"], v["code"], v["version"], strict=True)}
    spelled = dict(zip(v["customer_key"], v["customer_code"], strict=True))
    out = fact.copy()
    out["customer"] = ["unknown" if k == -1 else ident.get(k, f"dangling:{k}") for k in out["customer_key"]]
    # how the matched dimension row spells the code; carried for matchers, not compared
    out["dim_code"] = [spelled.get(k) for k in out["customer_key"]]
    return out


def _ts(v) -> str:
    # as text: 9999-12-31 does not fit pandas' nanosecond timestamps
    return v.strftime("%Y-%m-%d %H:%M:%S")


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in ("valid_from", "valid_to"):
        if c in df:
            df[c] = [_ts(v) for v in df[c]]
    for c in ("is_current", "product_key", "quantity", "order_id", "line_number"):
        if c in df:
            df[c] = df[c].astype("int64")
    for c in ("unit_price", "net_amount"):
        if c in df:
            df[c] = df[c].astype(float)
    if "order_date" in df:
        df["order_date"] = pd.to_datetime(df["order_date"]).dt.date
    return df


def _equal(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= AMOUNT_TOLERANCE or (pd.isna(a) and pd.isna(b))
    return a == b or (pd.isna(a) and pd.isna(b))


def _side(r: dict, keys: list[str], side: str) -> dict:
    """The merged row as one side saw it, without the suffixes."""
    return {k: r[k] for k in keys} | {c.removesuffix(f"_{side}"): v for c, v in r.items() if c.endswith(f"_{side}")}


def compare_table(name: str, old: pd.DataFrame, new: pd.DataFrame) -> list[Difference]:
    keys, cols = TABLES[name]
    old, new = _norm(old), _norm(new)
    diffs = []
    for side, df in (("legacy", old), ("new", new)):
        dup = df[df.duplicated(keys, keep=False)]
        for key, rows in dup.groupby(keys):
            diffs.append(Difference(name, tuple(key), f"duplicate_{side}", row={"rows": len(rows)}))
    old, new = old.drop_duplicates(keys), new.drop_duplicates(keys)
    m = old.merge(new, on=keys, how="outer", suffixes=("_legacy", "_new"), indicator=True)
    for r in m.to_dict("records"):
        key = tuple(r[k] for k in keys)
        if r["_merge"] != "both":
            side = "legacy" if r["_merge"] == "left_only" else "new"
            row = _side(r, keys, side)
            diffs.append(Difference(name, key, f"only_{side}", row=row))
            continue
        row = _side(r, keys, "new")
        for c in cols:
            a, b = r[f"{c}_legacy"], r[f"{c}_new"]
            if not _equal(a, b):
                diffs.append(Difference(name, key, "column", c, a, b, row))
    return diffs


def compare(old: dict[str, pd.DataFrame], new: dict[str, pd.DataFrame]) -> list[Difference]:
    diffs = compare_table("dim_customer", _versioned(old["dim_customer"]), _versioned(new["dim_customer"]))
    for t in ("fact_sales", "sales_rejected"):
        diffs += compare_table(
            t, _with_customer(old[t], old["dim_customer"]), _with_customer(new[t], new["dim_customer"])
        )
    return diffs


# ---------------------------------------------------------------- accepted differences


def _code_not_canonical(d: Difference) -> bool:
    """The order line's code differs from the dimension row's code only by
    case or trailing spaces: SSIS's Lookup missed it, the SQL join under a
    case-insensitive collation found it."""
    code, dim_code = d.row.get("customer_code"), d.row.get("dim_code")
    return (
        d.kind == "column"
        and d.column == "customer"
        and d.legacy == "unknown"
        and isinstance(code, str)
        and isinstance(dim_code, str)
        and code != dim_code
        and legacy.db_key(code) == legacy.db_key(dim_code)
        and d.new.startswith(legacy.db_key(code) + "#")
    )


def _load_time(d: Difference) -> bool:
    """A version stamped by the two platforms' own clocks on the same night:
    valid_from or valid_to differ by less than LOAD_WINDOW, and neither is
    the open end date."""
    if d.kind != "column" or d.column not in ("valid_from", "valid_to"):
        return False
    a, b = (datetime.strptime(str(x), "%Y-%m-%d %H:%M:%S") for x in (d.legacy, d.new))
    return OPEN_END not in (a, b) and abs(a - b) < LOAD_WINDOW


OPEN_END = datetime(9999, 12, 31)
LOAD_WINDOW = timedelta(hours=6)
MATCHERS = {"code_case_or_trailing_space": _code_not_canonical, "load_time": _load_time}


def load_accepted(path: Path = ACCEPTED) -> list[dict]:
    rules = yaml.safe_load(path.read_text())["accepted"]
    for r in rules:
        if r["matcher"] not in MATCHERS:
            raise ValueError(f"{path.name}: unknown matcher {r['matcher']!r}")
    return rules


def classify(diffs: list[Difference], rules: list[dict]) -> None:
    for d in diffs:
        for r in rules:
            if d.table in r["tables"] and MATCHERS[r["matcher"]](d):
                d.accepted_by = r["id"]
                break


# ---------------------------------------------------------------- report


def report(
    old: dict[str, pd.DataFrame], new: dict[str, pd.DataFrame], diffs: list[Difference], rules: list[dict]
) -> str:
    out = [
        "| Table | Legacy rows | New rows | Duplicate keys | Only legacy | Only new | Column differences | Accepted |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for t in TABLES:
        mine = [d for d in diffs if d.table == t]
        out.append(
            f"| {t} | {len(old[t]):,} | {len(new[t]):,} | {sum(d.kind.startswith('duplicate') for d in mine):,} | "
            f"{sum(d.kind == 'only_legacy' for d in mine):,} | {sum(d.kind == 'only_new' for d in mine):,} | "
            f"{sum(d.kind == 'column' for d in mine):,} | {sum(d.accepted_by is not None for d in mine):,} |"
        )
    for r in rules:
        n = sum(d.accepted_by == r["id"] for d in diffs)
        out.append(f"\nAccepted, {r['id']} ({r['adr']}): {n:,}")
    left = [d for d in diffs if d.accepted_by is None]
    out.append(f"\nUnexpected differences: {len(left):,}")
    for d in left[:20]:
        what = f"{d.column}: {d.legacy!r} -> {d.new!r}" if d.kind == "column" else d.kind
        out.append(f"  {d.table} {d.key}: {what}")
    if len(left) > 20:
        out.append(f"  ... and {len(left) - 20:,} more")
    return "\n".join(out)


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=11)
    a = p.parse_args()
    data = sample_data.generate(a.seed)
    old, new = run_legacy(data), run_new(data)
    rules = load_accepted()
    diffs = compare(old, new)
    classify(diffs, rules)
    print(report(old, new, diffs, rules))
    return 1 if any(d.accepted_by is None for d in diffs) else 0


if __name__ == "__main__":
    sys.exit(main())
