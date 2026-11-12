# 0003. Set-based procedures that can be rerun

Date: 2026-11-11
Status: Accepted

## Context

The SSIS packages process rows one at a time in places (the Slowly Changing
Dimension component and OLE DB Commands issue one statement per changed row)
and append without checking what is already loaded. Rerunning a failed SSIS
night meant knowing which package had committed what.

In ADF a failed activity is retried by the service (when a retry policy is
set) or rerun from the monitoring view by an operator, so every step must
give the same result if it runs twice.

## Decision

- **Dimensions.** `etl.usp_load_dim_customer` does what the SCD wizard was
  configured to do, as three statements in one transaction: overwrite the
  type 1 attribute (customer_name) on every version of the customer, expire
  the current row where a type 2 attribute (region, tier) changed, insert a
  new version for changed and new customers. A second run finds nothing
  changed and does nothing.
- **Facts.** `etl.usp_load_fact_sales` deletes the fact and reject rows for
  the order lines in staging, then inserts them. A rerun, or an order line
  that the ERP modified after it was loaded, replaces the row instead of
  adding a second one.
- **Staging** tables are truncated at the start of each staging pipeline.

## Consequences

- One statement per step instead of one per row; the procedures are short
  enough to review in one screen.
- The fact load now differs from SSIS when a line arrives twice: SSIS added a
  duplicate, the procedure keeps one. This is a fix, but it is a behaviour
  change, so it is written down here; the one-night reconciliation example
  does not exercise it (open issue 3).
- The type 1 rule was first written to update only the current row.
  Reconciliation reported 14 differences in dim_customer, because the SSIS
  wizard had "change all matching records" set. The procedure now matches
  SSIS, and a test pins the rule in both the procedure and its DuckDB copy.
