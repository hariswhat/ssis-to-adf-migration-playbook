# 0004. Accept the SQL collation's matching, after reconciliation shows it

Date: 2026-11-11
Status: Accepted

## Context

A full-cache SSIS Lookup compares strings in .NET: 'C00042', 'c00042' and
'C00042 ' are three different keys. The same join in SQL Server under the
database's case-insensitive collation (SQL_Latin1_General_CP1_CI_AS) treats
all three as equal, and `=` ignores trailing spaces.

Order lines typed in lowercase or padded by a CHAR column in the ERP were
loaded by SSIS against the unknown customer (-1). After the rewrite the same
lines find their customer.

Two choices:

1. Reproduce SSIS: join with a binary collation and also compare
   DATALENGTH (a binary collation alone still ignores trailing spaces), so
   these lines stay unknown.
2. Accept the new matching, and tell finance which sales move.

## Decision

Accept the new matching. These sales belong to the customer, and the unknown
member was hiding them. The difference is recorded in
`reconciliation/accepted.yml` with a matcher that only accepts a difference
when the order line's code differs from the dimension's by case or trailing
space and the old side was unknown. Any other difference still fails.

## Consequences

- In the reconciliation example 301 differences are accepted (298 in
  fact_sales and 3 in sales_rejected) and none are left over. Command in the
  README.
- Revenue by customer goes up for these customers and down for "Unknown" on
  the first night after cutover. Finance needs the reconciliation report
  before then, not after.
- The rule is general: wherever a Lookup is rewritten as a join, the
  assessment flags it (`semantics:` in `patterns.yml`), and its differences
  must be either fixed or accepted by an entry with an ADR, never ignored.
- History loaded before cutover still has these lines on -1. Restating it is
  a separate decision (open issue 4).
