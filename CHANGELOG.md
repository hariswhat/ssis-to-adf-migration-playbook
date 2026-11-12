# Changelog

Changes to this repository, newest first. Dates are commit dates.

## 2026-11-11

- README, with its numbers checked by `tests/test_readme.py`.
- Design decisions (six ADRs), diagrams, NFRs, cost method, runbook and open
  issues.
- The SQL Agent schedule becomes a schedule trigger, deployed stopped.
- `playbook.cost` counts activity runs per nightly run by integration runtime.
- The order load is rerunnable: Stage_Orders truncates staging, and the fact
  procedure replaces lines by key instead of appending.
- Type 1 name changes apply to every customer version, as the SSIS wizard
  did; found by reconciliation.
- Set-based procedures for the two transforming data flows, and a
  reconciliation example on generated data.
- First version: sample estate, .dtsx reader, assessment, waves, converter.
