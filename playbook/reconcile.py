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
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from . import legacy, sample_data

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = ROOT / "reconciliation" / "accepted.yml"
REFERENCE = ROOT / "sql" / "reference"

TABLES = {
    # table: (natural key, compared columns)
    "dim_customer": (["customer_code", "valid_from"], ["customer_name", "region", "tier", "valid_to", "is_current"]),
    "fact_sales": (["order_id", "line_number"], ["customer", "product_key", "quantity", "unit_price", "net_amount"]),
    "sales_rejected": (
        ["order_id", "line_number"],
        ["customer", "product_key", "quantity", "unit_price", "net_amount"],
    ),
}


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


def run_new(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    con = duckdb.connect()
    con.register("_dim", data["dim_customer_before"])
    con.execute("CREATE TABLE dim_customer AS SELECT * FROM _dim")
    for name in ("stg_customer", "dim_product", "stg_order_line"):
        con.register(f"_{name}", data[name])
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}")
    for f in ("load_dim_customer.sql", "load_fact_sales.sql"):
        for stmt in _statements((REFERENCE / f).read_text()):
            con.execute(stmt, {"load_ts": sample_data.LOAD_TS} if "$load_ts" in stmt else None)
    return {t: con.execute(f"SELECT * FROM {t}").df() for t in TABLES}


def _statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


# ---------------------------------------------------------------- compare


def _with_customer(fact: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    """Replace customer_key by the dimension row it points at."""
    ident = {
        k: f"{c}@{_ts(v)}" for k, c, v in zip(dim["customer_key"], dim["customer_code"], dim["valid_from"], strict=True)
    }
    out = fact.copy()
    out["customer"] = [ident.get(k, "unknown") if k != -1 else "unknown" for k in out["customer_key"]]
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
            df[c] = df[c].astype(float).round(2)
    return df


def _side(r: dict, keys: list[str], side: str) -> dict:
    """The merged row as one side saw it, without the suffixes."""
    return {k: r[k] for k in keys} | {c.removesuffix(f"_{side}"): v for c, v in r.items() if c.endswith(f"_{side}")}


def compare_table(name: str, old: pd.DataFrame, new: pd.DataFrame) -> list[Difference]:
    keys, cols = TABLES[name]
    old, new = _norm(old), _norm(new)
    m = old.merge(new, on=keys, how="outer", suffixes=("_legacy", "_new"), indicator=True)
    diffs = []
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
            if not (a == b or (pd.isna(a) and pd.isna(b))):
                diffs.append(Difference(name, key, "column", c, a, b, row))
    return diffs


def compare(old: dict[str, pd.DataFrame], new: dict[str, pd.DataFrame]) -> list[Difference]:
    diffs = compare_table("dim_customer", old["dim_customer"], new["dim_customer"])
    for t in ("fact_sales", "sales_rejected"):
        diffs += compare_table(
            t, _with_customer(old[t], old["dim_customer"]), _with_customer(new[t], new["dim_customer"])
        )
    return diffs


# ---------------------------------------------------------------- accepted differences


def _code_not_canonical(d: Difference) -> bool:
    """The order line's code differs from the dimension's only by case or
    trailing spaces: SSIS's Lookup missed it, the SQL join under a
    case-insensitive collation found it."""
    code = str(d.row.get("customer_code", ""))
    return (
        d.kind == "column"
        and d.column == "customer"
        and d.legacy == "unknown"
        and d.new != "unknown"
        and code != code.rstrip().upper()
        and str(d.new).startswith(code.rstrip().upper() + "@")
    )


MATCHERS = {"code_case_or_trailing_space": _code_not_canonical}


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
        "| Table | Legacy rows | New rows | Only legacy | Only new | Column differences | Accepted |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for t in TABLES:
        mine = [d for d in diffs if d.table == t]
        out.append(
            f"| {t} | {len(old[t]):,} | {len(new[t]):,} | "
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
