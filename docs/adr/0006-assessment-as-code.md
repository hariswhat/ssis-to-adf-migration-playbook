# 0006. The assessment is code, scored in points

Date: 2026-11-11
Status: Accepted

## Context

Migration estimates are usually a spreadsheet filled in by opening each
package. It goes stale as soon as a package changes, nobody can tell how a
number was reached, and the estimate is in days before anyone has migrated
anything.

## Decision

- `playbook/assess.py` reads every .dtsx file and scores each task, component,
  expression and event handler against `playbook/patterns.yml`. The rules,
  their targets and their weights are in one reviewed file.
- Effort is in points, a relative size. The team converts points to days
  after its first wave, from what that wave took. Nothing in this repo claims
  a number of days.
- Anything not in `patterns.yml` scores the unknown weight (5) and is listed
  for review instead of being guessed.
- The output is a markdown table for people and a CSV with one row per item
  for planning.

## Consequences

- Re-running the assessment after a package changes is one command, and a
  pattern weight that turns out wrong is fixed once for every package.
- The points are only as good as the weights. They rank packages and size
  waves against each other; they are not a forecast until calibrated.
- The reader understands the .dtsx constructs listed in `playbook/dtsx.py`.
  Packages from other estates may contain others; they are inventoried, and
  their unknown tasks show up in the CSV.
