# 0002. Transform in stored procedures, not Mapping Data Flows

Date: 2026-11-11
Status: Accepted

## Context

An SSIS data flow both moves and transforms rows. In ADF the movement is a
Copy activity. The transformation could go to:

- **Mapping Data Flows**, ADF's visual Spark-based transformations, the closest
  thing to an SSIS data flow;
- **T-SQL stored procedures** in the target Azure SQL Database, called by a
  Stored Procedure activity after the copy has landed the rows in staging.

Every transforming data flow in the estate reads from and writes to the same
warehouse database: lookups against dimensions, derived columns, a
conditional split, a slowly changing dimension.

## Decision

Copy activities land source rows in `stg` tables unchanged. Transformation
runs in stored procedures in the warehouse, one per rewritten data flow, kept
in `sql/procedures` and linted in CI. `patterns.yml` names the procedure that
replaces each data flow (`rewrites:`); the converter calls it.

## Consequences

- No Spark cluster start-up per run, and Data Flow compute is not billed.
  The work runs on the database the data is already in.
- The team that maintains the SSIS packages already reads T-SQL. Procedures
  diff in pull requests and can be run by hand during an incident.
- The rewrite is set-based instead of row-by-row, which changes behaviour in
  places (ADR-0003, ADR-0004). Reconciliation exists to find those places.
- The warehouse database carries the transformation load. If it becomes the
  bottleneck, the procedures are the place to tune (indexes on staging, batch
  sizes) before moving any of them to Spark.
- Sources that are not in the warehouse (files, the ERP) always go through a
  Copy activity to staging first. There is no cross-database join.
