"""What the SSIS packages Load_DimCustomer and Load_FactSales do, row by row,
in Python. This is the "before" side of the reconciliation example, written
from the package definitions:

  * Slowly Changing Dimension component: business key customer_code;
    historical attributes region and tier (expire the current row, insert a new
    version); changing attribute customer_name, applied by the OLE DB Command
    "UPDATE dw.dim_customer SET customer_name = ? WHERE customer_code = ?",
    which updates every version of the customer, not only the current one.
  * Lookup components with full cache: an exact .NET string comparison, so
    'C00042', 'c00042' and 'C00042 ' are three different keys. No match goes
    to -1 (Derived Column "Unknown members").
  * Conditional Split: quantity <= 0 goes to etl.sales_rejected.

It is an emulation, not SSIS. Its job is to make the behaviour differences
visible on known data, which reconciliation then finds on real data.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

FAR_FUTURE = datetime(9999, 12, 31)


def scd(dim: pd.DataFrame, stg: pd.DataFrame, load_ts: datetime) -> pd.DataFrame:
    dim = dim.copy()
    next_key = int(dim["customer_key"].max()) + 1
    new_rows = []
    current = {r.customer_code: r for r in dim[dim["is_current"] == 1].itertuples()}
    for s in stg.itertuples():
        cur = current.get(s.customer_code)  # exact match, as the component's lookup
        if cur is None:
            new_rows.append(
                {
                    "customer_key": next_key,
                    "customer_code": s.customer_code,
                    "customer_name": s.customer_name,
                    "region": s.region,
                    "tier": s.tier,
                    "valid_from": load_ts,
                    "valid_to": FAR_FUTURE,
                    "is_current": 1,
                }
            )
            next_key += 1
            continue
        if s.customer_name != cur.customer_name:
            # OLE DB Command: every version of the business key
            dim.loc[dim["customer_code"] == s.customer_code, "customer_name"] = s.customer_name
        if (s.region, s.tier) != (cur.region, cur.tier):
            idx = dim.index[(dim["customer_code"] == s.customer_code) & (dim["is_current"] == 1)]
            dim.loc[idx, ["valid_to", "is_current"]] = [load_ts, 0]
            new_rows.append(
                {
                    "customer_key": next_key,
                    "customer_code": s.customer_code,
                    "customer_name": s.customer_name,
                    "region": s.region,
                    "tier": s.tier,
                    "valid_from": load_ts,
                    "valid_to": FAR_FUTURE,
                    "is_current": 1,
                }
            )
            next_key += 1
    return pd.concat([dim, pd.DataFrame(new_rows)], ignore_index=True)


def fact_sales(
    lines: pd.DataFrame, dim_customer: pd.DataFrame, dim_product: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cust = dict(
        zip(
            dim_customer.loc[dim_customer["is_current"] == 1, "customer_code"],
            dim_customer.loc[dim_customer["is_current"] == 1, "customer_key"],
            strict=True,
        )
    )
    prod = dict(zip(dim_product["product_code"], dim_product["product_key"], strict=True))
    out = lines.copy()
    out["customer_key"] = [cust.get(c, -1) for c in out["customer_code"]]
    out["product_key"] = [prod.get(c, -1) for c in out["product_code"]]
    out["net_amount"] = (out["quantity"] * out["unit_price"]).round(2)
    rejected = out[out["quantity"] <= 0]
    kept = out[out["quantity"] > 0]
    cols = [
        "order_id",
        "line_number",
        "customer_code",
        "customer_key",
        "product_key",
        "order_date",
        "quantity",
        "unit_price",
        "net_amount",
    ]
    return kept[cols].reset_index(drop=True), rejected[cols].reset_index(drop=True)
