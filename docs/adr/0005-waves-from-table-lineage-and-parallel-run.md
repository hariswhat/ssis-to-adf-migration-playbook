# 0005. Migrate in waves ordered by table lineage, with a parallel run

Date: 2026-11-11
Status: Accepted

## Context

Moving every package on one night means one rollback for everything and no
way to tell which package caused a difference. Moving packages one at a time
in random order breaks dependencies: a rewritten fact load reading a
dimension still loaded by SSIS is fine, but two writers of one table on two
platforms is not.

## Decision

`playbook/waves.py` reads which tables each package reads and writes (SQL
text, source and destination tables, procedures it calls) and orders packages
into waves:

- a package moves in the same wave as, or after, every package that writes
  a table it reads;
- packages that write the same table move together;
- packages whose reads and writes cannot be seen (a Script Task) go last,
  after a manual check;
- orchestration packages (only Execute Package tasks) go after everything
  they call.

For the sample estate this gives five waves (README). Each wave then runs in
parallel with SSIS for at least five nights: both platforms load from the same
staging data into separate schemas and `playbook.reconcile` compares them.
A wave cuts over when every night reconciles with no unexpected differences
(runbook section 4).

## Consequences

- Each cutover is small, and its rollback is re-enabling the SSIS steps for
  the packages in that wave (runbook section 5).
- During the parallel run the warehouse does the work twice and needs a second
  schema for the new outputs.
- Lineage is read with regular expressions. Dynamic SQL and views hide
  dependencies; the wave list is checked by someone who knows the estate
  before it is used.
- Maintenance_Purge and Load_FactSales share a wave because both write
  etl.sales_rejected, even though one may never be migrated (ADR-0001).
