# 0001. Rewrite to ADF by default; the Azure-SSIS IR only as a bridge

Date: 2026-11-11
Status: Accepted

## Context

The estate is SSIS packages deployed to an on-premises SSIS catalog and run
by SQL Server Agent. The warehouse has moved to Azure SQL Database, and the
on-premises SQL Server running SSIS is to be retired. There are two ways to
get the packages off it:

1. **Lift and shift.** Deploy the same packages to an SSIS catalog in Azure
   and run them on an Azure-SSIS integration runtime, called from ADF with
   the Execute SSIS Package activity.
2. **Rewrite.** Replace each package with ADF pipelines: Copy activities for
   movement, stored procedures for transformation (ADR-0002).

Lift and shift is quick, but it keeps what makes SSIS expensive to own:
Visual Studio and SSDT to edit packages, VMs billed per node hour while the
IR is started, row-by-row components (OLE DB Command), and logic in XML that
reviewers cannot usefully diff.

## Decision

Rewrite is the end state for every package. The Azure-SSIS IR is used in two
cases, both temporary:

- **Packages with a blocker**, an item that cannot be rewritten mechanically
  (a Script Task or Script Component; `blocker: true` in
  `playbook/patterns.yml`). They stay lifted until someone rewrites them.
- **Packages whose wave has not cut over yet.** From the first night of the
  migration ADF orchestrates: `pl_master_nightly` calls `pl_<package>` for
  every package, and until a package's wave cuts over, `pl_<package>` is the
  lifted wrapper (`python -m playbook.convert ... --lift <package>`).
  Cutover replaces the wrapper with the rewritten pipeline (ADR-0005).

The lifted wrapper overrides the warehouse connection to Azure SQL with the
IR's managed identity. Other connections need a network path from the IR
(runbook section 1), and the converter lists them.

Packages that nothing schedules or calls are not migrated until an owner says
they are still needed (the assessment's "Check whether still needed").

## Consequences

- In the sample estate 6 of 8 packages are rewritten, 1 stays lifted
  (Export_PlantExtract, a C# Script Task) and 1 is checked
  (Maintenance_Purge). Commands in the README.
- The Azure-SSIS IR and an SSISDB run for the whole migration, not only for
  the blocked package. Removing the last lifted package removes both, which
  is the reason to keep rewriting after the last wave.
- The IR is billed while it is started, not per package run, so it has to be
  started before and stopped after the nightly run (runbook section 3).
- Lifted packages that read the ERP or the file share need the self-hosted
  IR configured as a data proxy for the Azure-SSIS IR, or a VNet-joined IR
  with a route to the network. Without one of them, lifting those packages
  fails.
- Script Tasks are judged by hand. Some are a few lines of file handling that
  an activity covers; others need an Azure Function.
