# Open issues

Known gaps, in rough priority order.

1. **Azure-SSIS IR start and stop are manual.** The master pipeline should
   start the IR (a Web activity calling the Data Factory REST API with the
   factory's managed identity) before the first lifted package and stop it
   after the last, or a separate schedule should. Until then the runbook
   covers it (section 3).
2. **The comparison has only run on generated data.** `playbook.reconcile`
   compares DataFrames; a runner that reads the two databases during a
   parallel run is not written. The comparison, the matchers and the report
   are the parts it would reuse. At large tables it should compare hashes
   per key range first.
3. **Reconciliation covers one night.** The rerun test repeats one night on
   the same staging data. A two-night example would exercise an order line
   modified after it was loaded, where the rewrite intentionally differs
   from SSIS (ADR-0003).
4. **History keeps the old lookup misses.** Fact rows loaded before cutover
   with a code that differs by case or trailing space stay on the unknown
   customer. Restating them is finance's decision.
5. **The alert URL is not a secret.** A Logic App HTTP trigger URL carries a
   signature, and the factory holds it as a plain global parameter. Read it
   from Key Vault in the pipeline (a Web activity before the alert), or
   protect the Logic App with Microsoft Entra authentication and call it
   with the factory's managed identity.
6. **Environment values are constants.** Server names, the lifted packages'
   warehouse connection and folder defaults are written into the generated
   JSON. A second environment (the parallel factory, test, production) needs
   them parameterised for deployment.
7. **Dealer files are archived before the merge.** As in the SSIS package,
   each file moves to the archive inside the loop, before
   `etl.usp_merge_dealer_target` runs. If the merge fails, a rerun finds no
   files. Archive after the merge succeeds.
8. **Type conversions are left to the Copy activity.** The SSIS Data
   Conversion (DT_NUMERIC(18,2), DT_DBDATE) in Import_DealerTargets becomes
   the Copy's implicit conversion to the staging column types. A value that
   SSIS rejected may convert differently; it needs an explicit mapping or a
   check in the merge.
9. **The converter knows this estate's patterns.** Expression translation
   covers string concatenation, comparisons, variables and project
   parameters; `+` is always read as concatenation, and SSIS escapes such as
   `\\` are not unescaped. Untranslated expressions, OR constraints, EXEC
   arguments and unmapped connections are listed as manual items. Data flows
   not named under `rewrites:` become calls to a placeholder procedure name,
   which is also listed.
10. **Lineage is regex-based.** Dynamic SQL, views and synonyms hide
    dependencies from the wave planner (ADR-0005).
11. **Points are uncalibrated.** They rank the packages; converting them to
    days needs a completed wave (ADR-0006).
12. **Nothing has been deployed.** The JSON in `adf/` is checked for internal
    consistency by tests, not by an ADF deployment. The first deployment to
    a dev factory is the real check.
13. **Export_PlantExtract stays lifted.** Its Script Task builds a
    fixed-width file. A rewrite would be a Copy activity to a DelimitedText
    or a small Function, and SFTP instead of FTP (ADF's FTP connector cannot
    be used as a sink).
