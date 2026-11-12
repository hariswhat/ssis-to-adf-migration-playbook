# ssis-to-adf-migration-playbook

Tools and a method for moving an SSIS estate to Azure Data Factory, worked
through on a sample estate: eight packages of a fictional manufacturer,
Northgate Building Products, written by `tools/make_sample_estate.py`. Every
package, table and number here is synthetic.

The method has four steps, each a module in `playbook/`:

1. **Assess** every package from its .dtsx file: what each task and component
   becomes in ADF, how much work that is in points, and whether anything
   blocks a rewrite. [ADR-0006]
2. **Plan waves** from table lineage, so that no table has writers on both
   platforms at once. [ADR-0005]
3. **Convert** the packages to ADF pipeline JSON. Data flows become Copy
   activities into staging plus set-based stored procedures; packages with a
   blocker run unchanged on an Azure-SSIS IR until they are rewritten.
   [ADR-0001, ADR-0002, ADR-0003]
4. **Reconcile** the old and new outputs during a parallel run. Every
   difference is either fixed or accepted in writing. [ADR-0004]

## The sample estate, assessed

`python -m playbook.assess estate/NorthgateETL`

| Package | Path | Complexity | Points | Blockers | Behaviour to reconcile |
|---|---|---|---|---|---|
| Export_PlantExtract | Run on Azure-SSIS IR, rewrite later | medium | 12 | Build extract file | - |
| Import_DealerTargets | Rewrite to ADF | medium | 11 | - | - |
| Load_DimCustomer | Rewrite to ADF | high | 16 | - | SCD |
| Load_FactSales | Rewrite to ADF | high | 16 | - | Lookup |
| Maintenance_Purge | Check whether still needed | low | 2 | - | - |
| Master_Nightly | Rewrite to ADF | medium | 8 | - | - |
| Stage_Customers | Rewrite to ADF | low | 5 | - | - |
| Stage_Orders | Rewrite to ADF | medium | 10 | - | - |

8 packages, 80 points. Points are relative size from `playbook/patterns.yml`,
not days; they are converted to days after the first wave.

Maintenance_Purge is marked for checking because no SQL Agent job runs it
(`estate/agent_jobs.yml`) and no package calls it.

## Waves

`python -m playbook.waves estate/NorthgateETL`

| Wave | Packages | Why here |
|---|---|---|
| 1 | Import_DealerTargets, Stage_Customers, Stage_Orders | Read only sources outside the warehouse |
| 2 | Load_DimCustomer | Reads stg.customer |
| 3 | Load_FactSales, Maintenance_Purge | Reads the dimension; both write etl.sales_rejected, so they move together |
| 4 | Export_PlantExtract | A Script Task hides what it reads and writes |
| 5 | Master_Nightly | Orchestration only; moves after everything it calls |

## Conversion

`python -m playbook.convert estate/NorthgateETL --out adf` writes the
factory into `adf/`: 8 pipelines, 4 linked services, 6 datasets, a self-hosted
and an Azure-SSIS integration runtime, the nightly trigger (from the SQL Agent
schedule) and a global parameter for alerts. A test fails if the committed
`adf/` differs from what the converter writes, and other tests check that
every reference in it resolves.

What is converted, and how:

| SSIS | ADF |
|---|---|
| Execute SQL Task | Script activity; Stored Procedure activity for `EXEC`; Lookup activity when the result sets a variable |
| Data flow, copy-shaped (source, row count, destination) | Copy activity; `rowsCopied` replaces the Row Count variable |
| Data flow that transforms | Stored Procedure activity calling the set-based rewrite in `sql/procedures` |
| ForEach file loop | Get Metadata, Filter, ForEach |
| File System Task (move) | Copy then Delete |
| Execute Package Task | Execute Pipeline (wait on completion) |
| Precedence constraint with an expression | If Condition |
| Variable and connection expressions | Pipeline expressions, Key Vault and parameters |
| OnError event handler | Web activity on each activity's failure path, posting to an alert Logic App |
| SQL Agent schedule | Schedule trigger |
| Package with a Script Task | Execute SSIS Package on the Azure-SSIS IR |

## Reconciliation example

`python -m playbook.reconcile` runs both sides on generated data: an emulation
of what the SSIS components do (`playbook/legacy.py`) and a DuckDB copy of the
new procedures (`sql/reference`). The data has 410 staged customers and 12,000
order lines, with problems planted where SSIS and SQL disagree.

| Table | SSIS rows | New rows | Differences | Accepted |
|---|---:|---:|---:|---:|
| dim_customer | 678 | 678 | 0 | 0 |
| fact_sales | 11,883 | 11,883 | 298 | 298 |
| sales_rejected | 117 | 117 | 3 | 3 |

The 301 accepted differences are order lines whose customer code differs from
the dimension only by case or a trailing space. The SSIS Lookup missed them
and loaded them against the unknown customer; the SQL join finds the
customer. That change was accepted (ADR-0004).

The first version of the dimension procedure applied the type 1 name change
to the current row only. Reconciliation reported 14 differences in
dim_customer, because the SSIS wizard was set to update every version. The
procedure was changed to match (ADR-0003, and the commit history).

## Cost

`python -m playbook.cost --files 1` counts what a nightly run is billed
for: 20 activity runs on the Azure IR and 6 on the self-hosted IR, plus 3
more on the self-hosted IR for each additional dealer file. The larger cost is
the Azure-SSIS IR's node hours and SSISDB while any package is lifted. Prices
are inputs, not stored; [docs/cost-estimate.md](docs/cost-estimate.md) has
the formula.

## Decisions

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-rewrite-by-default-azure-ssis-ir-as-a-bridge.md) | Rewrite by default; the Azure-SSIS IR only for packages with a blocker |
| [0002](docs/adr/0002-transform-in-sql-procedures-not-mapping-data-flows.md) | Transform in stored procedures, not Mapping Data Flows |
| [0003](docs/adr/0003-set-based-and-rerunnable-procedures.md) | Set-based procedures that can be rerun |
| [0004](docs/adr/0004-accept-collation-matching-after-reconciliation.md) | Accept the SQL collation's matching after reconciliation |
| [0005](docs/adr/0005-waves-from-table-lineage-and-parallel-run.md) | Waves from table lineage, parallel run per wave |
| [0006](docs/adr/0006-assessment-as-code.md) | Assessment as code, scored in points |

Also: [context](docs/architecture/c4-context.md) and
[container](docs/architecture/c4-container.md) diagrams,
[non-functional requirements](docs/non-functional-requirements.md),
[cost](docs/cost-estimate.md), [runbook](docs/runbook/README.md),
[open issues](docs/open-issues.md).

## Run it

```bash
make setup       # Python 3.11+, pinned requirements, pre-commit hooks
make test        # 50 tests, about 1 s
make assess      # table above, plus assessment.csv with one row per item
make waves
make convert     # regenerate adf/
make cost
make reconcile   # exits 1 on any difference not in reconciliation/accepted.yml
make lint        # ruff, sqlfluff on sql/procedures
```

## Known limitations

- Nothing has been deployed to Azure. The factory JSON is checked for
  internal consistency, not by an ADF deployment (open issue 8).
- The reconciliation has run only on generated data, for one night, against
  an emulation of SSIS rather than SSIS itself (open issues 2 and 3).
- The converter handles the constructs in this estate. Other expressions and
  data flows are listed for manual work rather than guessed (open issue 5).
- Lineage is read from SQL text with regular expressions; dynamic SQL and
  views are invisible to it.
- The Azure-SSIS IR is started and stopped by hand (open issue 1).

## What I'd change at 10x

Eighty packages instead of eight:

- **Metadata-driven staging.** Most staging packages are the same shape:
  truncate, copy, maybe a watermark. Instead of one generated pipeline per
  package, the converter would write rows to a control table read by one
  parameterised pipeline, and only the transforming packages would get their
  own pipelines.
- **Reconciliation against the warehouse.** A runner that reads both schemas
  in SQL, compares hashes per key range first and only pulls the rows that
  differ, so tables with hundreds of millions of rows can be compared nightly.
- **Pattern coverage from the estate.** Run the assessment first and sort the
  unknown items by count; extend `patterns.yml` and the converter in that
  order.
- **More than one team.** Waves give an order; at this size they would also
  be split by owning team, with a calendar of parallel runs so the warehouse
  is not running double loads for every wave at once.
- **Calibrated points.** After the first two waves, fit days per point by
  component type and publish the forecast with its error.

## Layout

```
estate/              sample SSIS project (.dtsx, Project.params) and SQL Agent jobs
tools/               writes the sample estate
playbook/            dtsx reader, assess, waves, convert, cost, reconcile, legacy emulation
playbook/patterns.yml  SSIS item -> ADF target, effort, blockers, semantics, rewrites
adf/                 generated factory JSON (do not edit by hand)
sql/procedures/      T-SQL that replaces the transforming data flows
sql/reference/       DuckDB copies of the procedures for the reconciliation example
reconciliation/      accepted differences
docs/                ADRs, architecture, NFRs, cost, runbook, open issues
tests/
```
