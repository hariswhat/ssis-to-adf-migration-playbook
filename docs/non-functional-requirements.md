# Non-functional requirements

Requirements set for the scenario, and where the design meets each. Nothing
here has run in Azure; the evidence is code and tests in this repo.

## Correctness

| Requirement | How | Evidence |
|---|---|---|
| The new pipelines load the same rows as SSIS | Parallel run, compared per table by key and column | `playbook/reconcile.py`, ADR-0005 |
| Behaviour changes are deliberate | Each accepted difference has a matcher and an ADR; anything else fails | `reconciliation/accepted.yml`, `test_unaccepted_difference_is_reported` |
| Behaviour differences are found before cutover | The assessment flags components whose semantics change (Lookup, SCD) | `semantics:` in `patterns.yml` |
| A rerun does not duplicate rows | Truncate staging; procedures replace by key | ADR-0003 |
| Generated pipelines are internally consistent | Tests: every dependsOn, dataset, linked service, IR, pipeline, parameter and variable reference resolves | `tests/test_convert.py` |

## Schedule and run time

| Requirement | Design |
|---|---|
| Warehouse loaded by 06:00 ET | Same start (01:30) and order as the SQL Agent job; no wave is cut over until its parallel run finishes inside the window for five nights |
| Late or missing dealer files do not fail the night | Get Metadata and Filter: no files means an empty ForEach |
| Lifted packages do not hold up the night | Export_PlantExtract runs last, as in Master_Nightly |

Run times are not measured here. The parallel run measures them (runbook
section 2).

## Operability

| Requirement | Design |
|---|---|
| On-call hears about failures | Each converted OnError handler becomes a Web activity on the failure path that posts to the alert Logic App |
| A failed night can be rerun from ADF monitoring | Rerunnable steps (ADR-0003) |
| Rollback per wave | SSIS steps disabled, not deleted, until the wave has run a month (runbook section 5) |

## Security

| Requirement | Design |
|---|---|
| No credentials in the factory JSON | ERP and file share secrets from Key Vault; Azure SQL by the factory's managed identity |
| On-premises sources not exposed to the internet | Self-hosted IR, outbound only |
| Least privilege on the warehouse | The factory identity gets EXECUTE on `etl`, the watermark table and `stg`; not db_owner (runbook section 1) |

## Cost

Activity runs per night are counted by `playbook.cost` (cost estimate). The
Azure-SSIS IR is the largest item while any package is lifted, so it runs
only during the nightly window (ADR-0001).
