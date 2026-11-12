# Cost estimate

How to price the converted factory, with the quantities this repo can count.
No prices are written here: ADF and Azure SQL prices change and vary by
region and agreement. Take them from the Azure pricing pages (Data Factory
pipeline pricing, and "Pricing example: Run SSIS packages on Azure-SSIS
integration runtime" on Microsoft Learn) on the day the estimate is made, and
record that date next to the result.

## What is billed

| Meter | Unit | Quantity for this estate | Source |
|---|---|---|---|
| Orchestration, Azure IR | per 1,000 activity runs | 16 per night | `python -m playbook.cost --files 1` |
| Orchestration, self-hosted IR | per 1,000 activity runs | 6 per night with one dealer file, plus 3 per extra file | same |
| Data movement, Azure IR | DIU-hours | none: every copy in this factory touches the ERP or the file share, so all of them run on the self-hosted IR | same |
| Data movement, self-hosted IR | hours of copy execution | 4 copies per night; duration measured in the parallel run | ADF monitoring |
| Pipeline activities (Lookup, Script, Stored Procedure, Get Metadata, Delete) | hours of execution | Duration measured in the parallel run | ADF monitoring |
| Azure-SSIS IR | node-hours while started | 1 node (Standard_D2_v3, Standard edition, licence included) × hours started per night | `adf/integrationRuntime/ir-azure-ssis.json` |
| SSISDB | Azure SQL Database tier | S1, while any package is lifted | same |
| Self-hosted IR | the Windows VM it runs on | existing on-premises VM, or one new VM | not an ADF charge |
| Warehouse | Azure SQL Database | Not changed by the migration, except during the parallel run (both loads) | |

The counts are for the converted factory as committed in `adf/` (only
Export_PlantExtract lifted), on a night where everything succeeds and every
If Condition is true. Failure alerts and the Fail activities after them
do not run on a good night and are not counted. `Maintenance_Purge` is not counted, because nothing calls it
(ADR-0001).

## Formula

```
monthly =
    nights × (azure_runs × price_azure_run + shir_runs × price_shir_run) / 1000
  + nights × shir_copy_hours × price_shir_movement_hour
  + nights × activity_hours × price_activity_hour
  + nights × ssis_ir_hours × nodes × price_ssis_node_hour
  + ssisdb_monthly
```

`playbook.cost` computes the first and fourth terms when given prices:

```bash
python -m playbook.cost --files 1 --nights 30 \
    --price-azure-run <per 1,000> --price-shir-run <per 1,000> \
    --ssis-hours <hours started per night> --price-ssis-node-hour <price>
```

## What dominates

The run counts are small: 22 activity runs a night is 660 in 30 nights.
While any package is lifted, the cost is the Azure-SSIS IR's node hours and
the SSISDB database, and both are fixed costs per night whether the packages
take one minute or fifty. During the migration every package not yet cut
over runs on the IR (ADR-0005), so the IR's hours per night are highest
before wave 1 and fall with each wave. That is what rewriting the last lifted
package saves, and why the IR is started only for the window it is needed
(runbook section 3).

## Parallel run

Each parallel night adds a database copy (`NorthgateDW_parallel`, billed as
a database of the same tier while it exists), a second factory's runs, and
the lifted packages' IR time a second time. Drop the copy between parallel
windows.

## What would change this

- Moving the ERP to Azure (or a private endpoint reachable from the Azure IR)
  moves the copies from the self-hosted IR to the Azure IR and its DIU-hour
  pricing.
- More dealer files per night add three self-hosted runs each.
- Rewriting Export_PlantExtract removes the Azure-SSIS IR and SSISDB lines.
