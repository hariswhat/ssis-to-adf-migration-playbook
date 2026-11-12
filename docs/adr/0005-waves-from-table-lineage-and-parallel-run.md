# 0005. Migrate in waves ordered by table lineage, with a parallel run

Date: 2026-11-11
Status: Accepted

## Context

Moving every package on one night means one rollback for everything and no
way to tell which package caused a difference. Moving packages one at a time
needs three things:

- an order that respects dependencies: two writers of one table must not be
  on two platforms, and a package must not run before the data it reads;
- one scheduler for both kinds of package, so a rewritten staging pipeline
  and the SSIS package that reads its output run in the right order;
- a way to compare old and new output on the same input before trusting the
  new one.

## Decision

**Order.** `playbook/waves.py` reads which tables each package reads and
writes (SQL text, source and destination tables, procedures it calls) and
orders packages into waves:

- a package moves in the same wave as, or after, every package that writes
  a table it reads;
- packages that write the same table move together;
- packages whose reads and writes cannot be seen (a Script Task) go last,
  after a manual check;
- orchestration packages are listed last, because they only call the
  others.

For the sample estate this gives five waves (README).

**One scheduler from the first night.** Before wave 1, every package is
deployed to the Azure-SSIS IR and ADF takes over scheduling:
`tr_nightly` runs `pl_master_nightly` (converted from Master_Nightly, never
lifted), which calls a lifted `pl_<package>` for each other package
(ADR-0001). The SQL Agent job is disabled. From then on,
cutting a wave over means replacing the lifted wrappers of that wave's
packages with their rewritten pipelines; the order of the night does not
change. Master_Nightly's own wave in the plan is then only a check that
nothing still depends on SSIS scheduling.

**Parallel run.** For at least five nights before a wave cuts over:

1. Just before the nightly run, copy the warehouse database
   (`CREATE DATABASE ... AS COPY OF`) to `NorthgateDW_parallel`.
2. The production factory runs the night as usual, with the wave's packages
   lifted, into `NorthgateDW`.
3. A second factory runs the whole night into `NorthgateDW_parallel`. Its
   JSON is generated with the wave's packages rewritten and the later waves
   still lifted, and every warehouse connection (the linked service and the
   lifted packages' override) points at the copy. Staging, watermarks and
   procedures are separate because the database is separate; the procedures
   need no changes.
4. `playbook.reconcile` compares the tables the wave writes between the two
   databases.

A wave cuts over when every night reconciles with no unexpected differences
(runbook section 4).

## Consequences

- Each cutover is small, and its rollback is redeploying the lifted wrapper
  for the wave's packages (runbook section 5).
- Every package has to run on the Azure-SSIS IR before its rewrite is ready,
  including the ones that read the ERP and the file share, so the IR's data
  proxy (ADR-0001) is the first thing to set up and test.
- The parallel run costs a database copy, a second factory and a second
  night's load, including the lifted packages' IR time, and
  the two sides read the ERP minutes apart, so a line changed in between
  shows as a difference. The load-time matcher accepts the different
  timestamps on new dimension versions; differences in data are looked at by
  hand (runbook section 6).
- The comparison has run only on generated data. Reading the two databases
  is open issue 2.
- Lineage is read with regular expressions. Dynamic SQL and views hide
  dependencies; the wave list is checked by someone who knows the estate
  before it is used.
- Maintenance_Purge and Load_FactSales share a wave because both write
  etl.sales_rejected, even though one may never be migrated (ADR-0001).
