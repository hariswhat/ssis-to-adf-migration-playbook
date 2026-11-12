# 0001. Rewrite to ADF by default; the Azure-SSIS IR only as a bridge

Date: 2026-11-11
Status: Accepted

## Context

The estate is SSIS packages deployed to an on-premises SSIS catalog and run
by SQL Server Agent. The warehouse is moving to Azure SQL Database and the
on-premises SQL Server running SSIS is to be retired. There are two ways to
get the packages off it:

1. **Lift and shift.** Deploy the same packages to an SSIS catalog in Azure
   and run them on an Azure-SSIS integration runtime, called from ADF with
   the Execute SSIS Package activity.
2. **Rewrite.** Replace each package with ADF pipelines: Copy activities for
   movement, stored procedures for transformation (ADR-0002).

Lift and shift is quick, but it keeps everything that makes SSIS expensive to
own: Visual Studio and SSDT to edit packages, a cluster of VMs billed per node
hour whenever it is running, row-by-row components (OLE DB Command), and logic
hidden in binary-ish XML that reviewers cannot diff.

## Decision

Rewrite is the default path. A package is lifted only when the assessment
finds a blocker, an item that cannot be rewritten mechanically (a Script Task
or Script Component; `blocker: true` in `playbook/patterns.yml`). Lifted
packages run on `ir-azure-ssis` through `pl_<package>` with one Execute SSIS
Package activity, so the master pipeline does not care which kind it calls,
and each lifted package stays on the backlog for a rewrite.

Packages that nothing schedules or calls are not migrated until an owner
says they are still needed (the assessment's "Check whether still needed").

## Consequences

- In the sample estate 6 of 8 packages are rewritten, 1 is lifted
  (Export_PlantExtract, a C# Script Task) and 1 is checked
  (Maintenance_Purge). Commands in the README.
- While one package is lifted, the factory has an Azure-SSIS IR and an SSISDB
  to run, patch and pay for. Removing the last lifted package removes both,
  which is the reason to keep rewriting after cutover.
- The IR is billed while it is started, not per package run, so it has to be
  started before and stopped after the nightly run (runbook section 3).
- Script Tasks are judged by hand. Some are a few lines of file handling that
  an activity covers; others need an Azure Function.
