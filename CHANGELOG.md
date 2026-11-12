# Changelog

Changes to this repository, newest first. Dates are commit dates.

## 2026-11-12 (second review)

- The watermark move into the fact procedure applied even while
  Load_FactSales was lifted, so between waves 1 and 3 nothing moved it and
  the SSIS fact load appended the same lines every night. A moved task is
  now left out only once the package taking it over is rewritten.
- One run time per night: the master passes its trigger time to every
  child as `run_time`, which the dimension procedure uses as its load time.
- The application lock in the dimension procedure has a timeout and its
  result is checked.
- Variables nothing reads are dropped from the converted pipelines; the
  trigger run is counted in the cost.
- Docs: the parallel factory needs its own dealer file folder, no plant
  extract and its own Azure-SSIS IR; the grants lifted packages need; lifted
  dimension loads stamp UTC; a trailing-space-only change is lost; failures
  alert twice (open issues 14 to 16).

## 2026-11-11 (review fixes)

A review of the factory, the procedures and the reconciliation found these;
fixed unless listed in `docs/open-issues.md`.

- Failure alerts made failed runs succeed: a Fail activity now follows each
  alert.
- Order lines could be lost when the fact load failed after Stage_Orders had
  moved the watermark; the fact procedure now moves it in its transaction.
  Staging truncates become pre-copy scripts, and the fact procedure keeps
  the latest staged row per order line.
- The dimension procedure compares attributes case-sensitively and
  NULL-safely, takes its load time from the pipeline in Eastern time, and
  serialises runs with an application lock.
- Reconciliation reports duplicate keys and dangling fact keys, compares
  every column, compares amounts without rounding, and matches dimension
  versions by position in history instead of timestamp. The DuckDB reference
  SQL now follows both procedures statement by statement, and a test runs it
  twice to check it is rerunnable.
- The SCD emulation matches business keys as the database does; the sample
  data stages three customer codes in lowercase.
- The sample Load_FactSales package sent Lookup misses to an unconnected
  output and had no Rejected path; fixed in the generator.
- The parallel run was not workable as written (shared staging and
  watermark, no common scheduler). ADF now schedules from the first night,
  with packages not yet cut over lifted (`--lift`), and the parallel run
  uses a database copy and a second factory.
- Lifted packages get their warehouse connection overridden to Azure SQL
  with managed identity; other connections are listed as manual work.
- Linked services use the current shape (server, database, encrypt); the
  dealer folders no longer repeat the share name; the flat file name column
  is kept; constraint EvalOp and LogicalAnd are read; EXEC arguments,
  unmapped connections and Lookups on the ERP are handled or listed.

## 2026-11-11

- README, with its numbers checked by `tests/test_readme.py`.
- Design decisions (six ADRs), diagrams, NFRs, cost method, runbook and open
  issues.
- The SQL Agent schedule becomes a schedule trigger, deployed stopped.
- `playbook.cost` counts activity runs per nightly run by integration runtime.
- The order load is rerunnable: Stage_Orders truncates staging, and the fact
  procedure replaces lines by key instead of appending.
- Type 1 name changes apply to every customer version, as the SSIS wizard
  did; found by reconciliation.
- Set-based procedures for the two transforming data flows, and a
  reconciliation example on generated data.
- First version: sample estate, .dtsx reader, assessment, waves, converter.
