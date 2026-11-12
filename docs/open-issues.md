# Open issues

Known gaps, in rough priority order.

1. **Azure-SSIS IR start and stop are manual.** The master pipeline should
   start the IR (a Web activity calling the Data Factory REST API with the
   factory's managed identity) before Export_PlantExtract and stop it after,
   or a separate schedule should. Until then the runbook covers it
   (section 3).
2. **The comparison has only run on generated data.** `playbook.reconcile`
   compares DataFrames; a runner that reads the two schemas from the
   warehouse during a parallel run is not written. The comparison itself,
   the matchers and the report are the parts that would be reused.
3. **Reconciliation covers one night.** A two-night example would exercise
   the rerunnable fact load (an order line modified after it was loaded),
   where the rewrite intentionally differs from SSIS (ADR-0003).
4. **History keeps the old lookup misses.** Fact rows loaded before cutover
   with a lowercase or padded customer code stay on the unknown customer.
   Restating them is finance's decision.
5. **The converter knows this estate's patterns.** Expression translation
   covers string concatenation, comparisons, variables and project
   parameters; anything else returns nothing and the activity is listed for
   manual work. Data flows not named under `rewrites:` become calls to a
   placeholder procedure name.
6. **Lineage is regex-based.** Dynamic SQL, views and synonyms hide
   dependencies from the wave planner (ADR-0005).
7. **Points are uncalibrated.** They rank the packages; converting them to
   days needs a completed wave (ADR-0006).
8. **Nothing has been deployed.** The JSON in `adf/` is checked for internal
   consistency by tests, not by an ADF deployment. The first deployment to a
   dev factory is the real check.
9. **Export_PlantExtract stays lifted.** Its Script Task builds a fixed-width
   file. A rewrite would be a Copy activity to a DelimitedText or a small
   Function, and SFTP instead of FTP (ADF's FTP connector cannot be used as
   a sink).
