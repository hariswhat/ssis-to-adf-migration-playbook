"""Small synthetic data for the reconciliation example: one night's staging
data for the customer dimension and the sales fact, and the dimension as it
stood before the load.

Planted on purpose, because they are where SSIS and set-based SQL disagree:
  * order lines whose customer_code differs from the dimension only in case or
    trailing spaces (a full-cache Lookup misses them; a SQL join does not)
  * customers whose name changed (a type 1 attribute) and who already have
    several versions in the dimension
  * customers whose region or tier changed (type 2)
  * order lines with zero or negative quantity (rejected)

    python -m playbook.sample_data --out data
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REGIONS = ["Midwest", "Northeast", "South", "West"]
TIERS = ["Bronze", "Silver", "Gold", "Platinum"]
LOAD_TS = datetime(2026, 3, 2, 1, 30)


def generate(seed: int = 11, customers: int = 400, lines: int = 12_000) -> dict[str, pd.DataFrame]:
    rng = random.Random(seed)
    dim_rows, key = [], 0
    current = {}
    for i in range(1, customers + 1):
        code = f"C{i:05d}"
        name, region, tier = f"Dealer {i:05d}", rng.choice(REGIONS), rng.choice(TIERS)
        versions = rng.choice([1, 1, 1, 2, 3])
        start = datetime(2022, 1, 1)
        for v in range(versions):
            key += 1
            end = start + timedelta(days=rng.randint(120, 400)) if v < versions - 1 else datetime(9999, 12, 31)
            if v > 0:
                region, tier = rng.choice(REGIONS), rng.choice(TIERS)
            dim_rows.append(
                {
                    "customer_key": key,
                    "customer_code": code,
                    "customer_name": name,
                    "region": region,
                    "tier": tier,
                    "valid_from": start,
                    "valid_to": end,
                    "is_current": int(v == versions - 1),
                }
            )
            start = end
        current[code] = (name, region, tier)
    dim_before = pd.DataFrame(dim_rows)

    stg_rows = []
    for code, (name, region, tier) in current.items():
        r = rng.random()
        if r < 0.05:
            name = name + " LLC"  # type 1 change
        elif r < 0.09:
            region = rng.choice([x for x in REGIONS if x != region])  # type 2 change
        elif r < 0.11:
            tier = rng.choice([x for x in TIERS if x != tier])
        stg_rows.append(
            {
                "customer_code": code,
                "customer_name": name,
                "region": region,
                "tier": tier,
                "modified_at": LOAD_TS - timedelta(hours=3),
            }
        )
    for i in range(customers + 1, customers + 11):  # new customers
        stg_rows.append(
            {
                "customer_code": f"C{i:05d}",
                "customer_name": f"Dealer {i:05d}",
                "region": rng.choice(REGIONS),
                "tier": "Bronze",
                "modified_at": LOAD_TS - timedelta(hours=2),
            }
        )
    stg_customer = pd.DataFrame(stg_rows)

    dim_product = pd.DataFrame([{"product_key": k, "product_code": f"P{k:04d}"} for k in range(1, 201)])

    codes = stg_customer["customer_code"].tolist()
    ol = []
    for n in range(1, lines + 1):
        code = rng.choice(codes)
        r = rng.random()
        if r < 0.015:
            code = code.lower()  # 'c00042'
        elif r < 0.025:
            code = code + " "  # trailing space from a CHAR column
        qty = rng.randint(1, 40) if rng.random() > 0.01 else rng.choice([0, -1, -3])
        ol.append(
            {
                "order_id": 500_000 + n // 3,
                "line_number": n % 3 + 1,
                "customer_code": code,
                "product_code": f"P{rng.randint(1, 205):04d}",  # a few unknown products
                "order_date": date(2026, 3, 1),
                "quantity": qty,
                "unit_price": round(rng.uniform(5, 400), 2),
                "modified_at": LOAD_TS - timedelta(minutes=rng.randint(10, 600)),
            }
        )
    stg_order_line = pd.DataFrame(ol)
    return {
        "dim_customer_before": dim_before,
        "stg_customer": stg_customer,
        "dim_product": dim_product,
        "stg_order_line": stg_order_line,
    }


def write(out: Path, seed: int = 11) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, df in generate(seed).items():
        df.to_parquet(out / f"{name}.parquet", index=False)
        counts[name] = len(df)
    return counts


def main() -> None:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("data"))
    for k, v in write(p.parse_args().out).items():
        print(f"{k:<22}{v:>8,}")


if __name__ == "__main__":
    main()
