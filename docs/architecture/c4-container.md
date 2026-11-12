# Containers

## The factory

```mermaid
C4Container
    title adf-ngbp-etl - containers

    System_Boundary(adf, "Data Factory") {
        Container(master, "pl_master_nightly", "Pipeline", "Calls each wave's pipelines in the order Master_Nightly did")
        Container(stage, "pl_stage_customers, pl_stage_orders", "Pipelines", "Truncate stage, copy from ERP; watermark for order lines")
        Container(load, "pl_load_dimcustomer, pl_load_factsales", "Pipelines", "One Stored Procedure activity each; Web activity alert on failure")
        Container(files, "pl_import_dealertargets", "Pipeline", "Get Metadata, Filter, ForEach: copy CSV, archive, merge")
        Container(lift, "pl_export_plantextract", "Pipeline", "Execute SSIS Package on ir-azure-ssis")
        Container(gp, "alertLogicAppUrl", "Global parameter", "Where failure alerts are posted")
    }

    ContainerDb(dw, "NorthgateDW", "Azure SQL Database", "stg, dw, etl schemas; etl.usp_load_* procedures; SSISDB catalog on the same server")
    Container(shir, "ir-selfhosted", "Self-hosted IR", "ERP and file share access")
    Container(ssisir, "ir-azure-ssis", "Azure-SSIS IR", "1 node, Standard edition")
    Container(kv, "Key Vault", "ls_keyvault", "ERP and file share credentials")

    Rel(master, stage, "Execute Pipeline")
    Rel(master, load, "Execute Pipeline")
    Rel(master, files, "Execute Pipeline")
    Rel(master, lift, "Execute Pipeline")
    Rel(stage, shir, "Copy via")
    Rel(files, shir, "Copy, Delete via")
    Rel(stage, dw, "Writes stg")
    Rel(load, dw, "Calls procedures")
    Rel(lift, ssisir, "Runs package on")
    Rel(shir, kv, "Credentials from")
```

## The playbook

How the tools in this repository feed each other.

```mermaid
flowchart LR
    dtsx[".dtsx packages<br/>+ agent_jobs.yml"] --> reader["playbook.dtsx<br/>reader"]
    patterns["patterns.yml"] --> assess
    reader --> assess["playbook.assess<br/>points, path"]
    reader --> waves["playbook.waves<br/>lineage, waves"]
    assess --> convert["playbook.convert<br/>pipelines, IRs, datasets"]
    patterns --> convert
    convert --> adf["adf/<br/>factory JSON"]
    adf --> cost["playbook.cost<br/>activity runs"]
    procs["sql/procedures"] --> recon["playbook.reconcile"]
    legacy["playbook.legacy<br/>SSIS behaviour"] --> recon
    accepted["reconciliation/<br/>accepted.yml"] --> recon
```

`adf/` is generated: `make convert` rewrites it, and a test fails if the
committed files differ from what the converter produces. Hand changes belong
in the converter or in `patterns.yml`.
