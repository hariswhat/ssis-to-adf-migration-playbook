import json

import pytest
from conftest import ESTATE, ROOT

from playbook.convert import convert_project, support_files, translate, write


@pytest.fixture(scope="module")
def results():
    return {r.package: r for r in convert_project(ESTATE)}


def test_generated_adf_matches_committed(tmp_path, results):
    write(list(results.values()), tmp_path)
    produced = {p.relative_to(tmp_path): p.read_text() for p in tmp_path.rglob("*.json")}
    committed = {p.relative_to(ROOT / "adf"): p.read_text() for p in (ROOT / "adf").rglob("*.json")}
    assert produced == committed


def _activities(acts):
    for a in acts:
        yield a
        tp = a.get("typeProperties", {})
        yield from _activities(tp.get("activities", []))
        yield from _activities(tp.get("ifTrueActivities", []))
        yield from _activities(tp.get("ifFalseActivities", []))


def test_depends_on_names_a_sibling(results):
    def check(acts):
        names = {a["name"] for a in acts}
        assert len(names) == len(acts), "duplicate activity names"
        for a in acts:
            for d in a["dependsOn"]:
                assert d["activity"] in names, (a["name"], d["activity"])
            tp = a.get("typeProperties", {})
            for key in ("activities", "ifTrueActivities", "ifFalseActivities"):
                if tp.get(key):
                    check(tp[key])

    for r in results.values():
        check(r.pipeline["properties"]["activities"])


def test_references_resolve(results):
    files = support_files()
    datasets = {b["name"] for k, b in files.items() if k.startswith("dataset/")}
    linked = {b["name"] for k, b in files.items() if k.startswith("linkedService/")}
    irs = {b["name"] for k, b in files.items() if k.startswith("integrationRuntime/")}
    pipelines_ = {r.pipeline["name"] for r in results.values()}
    for ref in _refs(files["trigger/tr_nightly.json"], "PipelineReference"):
        assert ref in pipelines_
    pipelines = {r.pipeline["name"] for r in results.values()}
    for k, b in files.items():
        if k.startswith("dataset/"):
            assert b["properties"]["linkedServiceName"]["referenceName"] in linked
        via = b.get("properties", {}).get("connectVia")
        if via:
            assert via["referenceName"] in irs
    text = json.dumps([r.pipeline for r in results.values()])
    for kind, known in (
        ("DatasetReference", datasets),
        ("PipelineReference", pipelines),
        ("IntegrationRuntimeReference", irs),
        ("LinkedServiceReference", linked),
    ):
        for ref in _refs(json.loads(text), kind):
            assert ref in known, (kind, ref)


def _refs(node, kind):
    if isinstance(node, dict):
        if node.get("type") == kind and "referenceName" in node:
            yield node["referenceName"]
        for v in node.values():
            yield from _refs(v, kind)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v, kind)


def test_blocked_package_is_lifted(results):
    r = results["Export_PlantExtract"]
    (act,) = r.pipeline["properties"]["activities"]
    assert act["type"] == "ExecuteSSISPackage"
    assert r.manual == ["blocked: Build extract file"]


def test_rewrites_call_named_procedures(results):
    acts = list(_activities(results["Load_FactSales"].pipeline["properties"]["activities"]))
    procs = [a["typeProperties"]["storedProcedureName"] for a in acts if a["type"] == "SqlServerStoredProcedure"]
    assert "etl.usp_load_fact_sales" in procs
    for name in ("usp_load_fact_sales", "usp_load_dim_customer"):
        assert (ROOT / "sql" / "procedures" / f"{name}.sql").exists()


def test_master_calls_child_pipelines(results):
    acts = results["Master_Nightly"].pipeline["properties"]["activities"]
    calls = [a for a in acts if a["type"] == "ExecutePipeline"]
    assert len(calls) == 6
    assert all(a["typeProperties"]["waitOnCompletion"] for a in calls)


@pytest.mark.parametrize(
    "ssis, adf",
    [
        ('"a" + @[User::X]', "concat('a', variables('X'))"),
        ("@[User::RowsCopied] > 0", "greater(variables('RowsCopied'), 0)"),
        ("@[$Project::DW_ConnectionString]", "pipeline().globalParameters.DW_ConnectionString"),
        ('"it\'s"', "'it''s'"),
        ("@[User::N] != 3", "not(equals(variables('N'), 3))"),
    ],
)
def test_translate(ssis, adf):
    assert translate(ssis) == adf


def test_translate_row_count_variable():
    assert (
        translate("@[User::RowsCopied] > 0", {"RowsCopied": "Copy"}) == "greater(activity('Copy').output.rowsCopied, 0)"
    )


@pytest.mark.parametrize("ssis", ["(DT_WSTR, 10) @[User::X]", "@[User::A] * 2", "SUBSTRING(@[User::X], 1, 2)"])
def test_translate_gives_up(ssis):
    assert translate(ssis) is None


def test_global_parameters_and_variables_are_declared(results):
    import re

    factory = next(b for k, b in support_files().items() if k.startswith("factory/"))
    globals_ = set(factory["properties"]["globalParameters"])
    for r in results.values():
        text = json.dumps(r.pipeline)
        props = r.pipeline["properties"]
        assert set(re.findall(r"globalParameters\.(\w+)", text)) <= globals_
        assert set(re.findall(r"pipeline\(\)\.parameters\.(\w+)", text)) <= set(props.get("parameters", {}))
        assert set(re.findall(r"variables\('(\w+)'\)", text)) <= set(props.get("variables", {}))


def test_trigger_follows_the_agent_schedule(tmp_path):
    from playbook.convert import nightly_trigger

    rec = nightly_trigger()["properties"]["typeProperties"]["recurrence"]
    assert (rec["schedule"]["hours"], rec["schedule"]["minutes"]) == ([1], [30])
    jobs = tmp_path / "jobs.yml"
    jobs.write_text(
        "jobs:\n  - name: x\n    schedule: weekly 02:00\n    steps:\n      - package: P/Master_Nightly.dtsx\n"
    )
    with pytest.raises(ValueError, match="only daily"):
        nightly_trigger(jobs)
