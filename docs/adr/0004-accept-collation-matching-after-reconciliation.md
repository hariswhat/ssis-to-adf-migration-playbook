# 0004. Accept the SQL collation's matching, after reconciliation shows it

Date: 2026-11-11
Status: Accepted

## Context

A full-cache SSIS Lookup compares strings in .NET: 'C00042', 'c00042' and
'C00042 ' are three different keys. The same join in SQL Server under the
database's case-insensitive collation (SQL_Latin1_General_CP1_CI_AS) treats
all three as equal, and `=` ignores trailing spaces.

Order lines typed in lowercase or padded by a CHAR column in the ERP were
loaded by SSIS against the unknown customer (-1). So were the lines of a
customer whose current dimension row was created from a staged code in
lowercase: the Slowly Changing Dimension component looks the business key up
in the database, so it matched the existing customer and inserted the new
version with the staged spelling, and the cached Lookup then missed every
order line in uppercase. After the rewrite all of these lines find their
customer.

Attribute changes are a separate case: the SCD component compares them in
.NET, case-sensitively, and the procedure does the same with a binary
collation. Neither detects a change in trailing spaces alone, because SQL
Server's = ignores them under every collation.

Two choices:

1. Reproduce SSIS: join with a binary collation and also compare
   DATALENGTH (a binary collation alone still ignores trailing spaces), so
   these lines stay unknown.
2. Accept the new matching, and tell finance which sales move.

## Decision

Accept the new matching. These sales belong to the customer, and the unknown
member was hiding them. The difference is recorded in
`reconciliation/accepted.yml` with a matcher that only accepts a difference
when the old side was unknown and the order line's code differs from the
matched dimension row's code only by case or trailing spaces. Any other
difference still fails.

## Consequences

- In the reconciliation example 389 differences are accepted (384 in
  fact_sales and 5 in sales_rejected) and none are left over. Command in the
  README.
- Revenue by customer goes up for these customers and down for "Unknown" on
  the first night after cutover. Finance needs the reconciliation report
  before then, not after.
- The rule is general: wherever a Lookup is rewritten as a join, the
  assessment flags it (`semantics:` in `patterns.yml`), and its differences
  must be either fixed or accepted by an entry with an ADR, never ignored.
- History loaded before cutover still has these lines on -1. Restating it is
  a separate decision (open issue 4).
