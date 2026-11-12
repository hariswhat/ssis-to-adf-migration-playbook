# Context

Northgate Building Products is fictional. The SQL Server names in the sample
packages (ERPSQL01, DWSQL01, fileshare01) and the Azure resource names in
`adf/` are made up.

## Before

```mermaid
C4Context
    title Nightly ETL before migration

    Person(ops, "ETL on-call", "Watches the SQL Agent job, reruns packages")
    System_Ext(erp, "ERP", "SQL Server on premises: customers, order lines")
    System_Ext(share, "File share", "Dealer target CSVs dropped by sales ops")
    System_Ext(partner, "Plant partner", "Receives a nightly extract over FTP")

    System_Boundary(onprem, "On premises") {
        System(ssis, "SSIS catalog + SQL Agent", "NorthgateETL project, 8 packages, Master_Nightly at 01:30")
        SystemDb(dw, "NorthgateDW", "SQL Server warehouse")
    }

    Rel(ssis, erp, "Reads")
    Rel(ssis, share, "Reads and archives CSVs")
    Rel(ssis, dw, "Stages and loads")
    Rel(ssis, partner, "Uploads extract")
    Rel(ops, ssis, "Monitors")
```

## After

```mermaid
C4Context
    title Nightly ETL after migration

    Person(ops, "ETL on-call", "ADF monitoring, alerts from the Logic App")
    System_Ext(erp, "ERP", "SQL Server on premises")
    System_Ext(share, "File share", "On premises")
    System_Ext(partner, "Plant partner", "FTP")

    System_Boundary(az, "Azure") {
        System(adf, "Data Factory", "Pipelines converted from the packages; trigger at 01:30")
        SystemDb(dw, "NorthgateDW", "Azure SQL Database; stored procedures replace data flows")
        System(ssisir, "Azure-SSIS IR", "Runs packages that are not rewritten yet")
    }
    System(shir, "Self-hosted IR", "Windows VM on premises; reaches ERP and the file share")

    Rel(adf, shir, "Copy, Get Metadata, Delete")
    Rel(shir, erp, "Reads")
    Rel(shir, share, "Reads and archives CSVs")
    Rel(adf, dw, "Script and Stored Procedure activities")
    Rel(adf, ssisir, "Execute SSIS Package")
    Rel(ssisir, partner, "Uploads extract (Export_PlantExtract)")
    Rel(ops, adf, "Monitors")
```
