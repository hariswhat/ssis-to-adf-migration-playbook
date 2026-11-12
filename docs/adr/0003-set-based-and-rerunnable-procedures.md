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
  new version for changed and new customers. Attribute changes are compared
  case-sensitively and NULL-safely, as the component did. A second run finds
  nothing changed and does nothing. An application lock serialises runs,
  because new keys are `MAX + ROW_NUMBER`.
- **Load time.** The pipeline passes `@load_ts` as the trigger time in
  Eastern time, the clock SSIS's `GETDATE()` used on the on-premises server,
  so new history stays on the same clock as the old, and every row a run
  writes carries one time.
- **Facts.** `etl.usp_load_fact_sales` reduces staging to the latest row
  per order line, deletes the fact and reject rows for those lines, then
  inserts them. A rerun, or an order line the ERP modified after it was
  loaded, replaces the row instead of adding a second one.
- **Watermark.** Stage_Orders moved the order watermark forward as soon as
  it had staged rows; if the fact load then failed, the next night truncated
  staging and those lines were never loaded. The watermark now moves in the
  fact procedure's transaction, and the converter leaves the stage
  pipeline's "Set watermark" out (`moved:` in `patterns.yml`).
- **Staging.** A task that only truncates the table a later Copy loads
  becomes the Copy's pre-copy script, so rerunning a failed Copy from ADF
  monitoring truncates again instead of appending to a half-loaded table.

## Consequences

- One statement per step instead of one per row; the procedures are short
  enough to review in one screen.
- The fact load differs from SSIS when a line arrives twice: SSIS added a
  duplicate, the procedure keeps one. This is a fix, but it is a behaviour
  change, so it is written down here. `tests/test_reconcile.py` runs the
  reference SQL twice on the same staging data and checks nothing changes;
  a two-night example is open issue 3.
- The type 1 rule was first written to update only the current row.
  Reconciliation reported 14 differences in dim_customer (commit "Add
  set-based rewrites and a reconciliation example"), because the SSIS wizard
  had "change all matching records" set. The procedure now matches SSIS, and
  a test pins the rule in both the procedure and its DuckDB copy.
- A failed fact load no longer loses order lines, but staging is reloaded
  from the old watermark the next night, so that night's copy is larger.
