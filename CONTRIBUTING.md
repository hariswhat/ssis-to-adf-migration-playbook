# Contributing

Conventions for this repository: how to add a pattern, a rewrite or an
accepted difference, and code style.

## Adding a pattern

1. Add the SSIS task or component class to `playbook/patterns.yml` with its
   ADF target, effort in points and, if it cannot be rewritten mechanically,
   `blocker: true`. If its behaviour differs from the target's, describe it
   under `semantics:`; the assessment lists it and reconciliation must cover
   it.
2. If the converter should handle it, add the case to `playbook/convert.py`
   and a test in `tests/test_convert.py`.
3. Run `make convert` and commit the regenerated `adf/` in the same commit.

## Adding a set-based rewrite

1. Write the procedure in `sql/procedures/` (T-SQL, linted by sqlfluff).
   It must be rerunnable (ADR-0003).
2. Name it under `rewrites:` in `patterns.yml` as `<package>/<data flow>`,
   with any parameters the pipeline passes. A task whose work the procedure
   takes over goes under `moved:`.
3. For the reconciliation example, add a DuckDB copy in `sql/reference/`
   with the same data statements in the same order, and add the pair to
   `test_reference_sql_follows_the_procedure`.

## Accepting a difference

Only with an ADR that says why the new behaviour is right. Add a matcher to
`MATCHERS` in `playbook/reconcile.py` that accepts exactly those differences,
a test that it rejects others, and an entry in
`reconciliation/accepted.yml`.

## Style

- Python: `ruff` for lint and format, line length 120.
- SQL: keywords upper case, identifiers snake_case, leading commas; sqlfluff
  with the `tsql` dialect (`.sqlfluff`).
- `adf/` is generated. Change the converter or `patterns.yml`, never the JSON.

## Commits

- Imperative subject, about 50 characters, no trailing period.
- Body explains why, wrapped at 72.

## Documentation

- ADRs: context, decision, consequences, including the costs the decision
  accepts.
- Numbers in the README come from a command in this repo, and
  `tests/test_readme.py` checks the main ones.
