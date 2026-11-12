from conftest import ESTATE

from playbook.assess import CHECK, LIFT, REWRITE, assess_package, assess_project, markdown, scheduled_packages


def by_name():
    return {a.package: a for a in assess_project(ESTATE)}


def test_paths():
    a = by_name()
    assert a["Export_PlantExtract"].path == LIFT  # Script Task
    assert a["Maintenance_Purge"].path == CHECK  # nothing runs it
    assert {n for n, x in a.items() if x.path == REWRITE} == {
        "Import_DealerTargets",
        "Load_DimCustomer",
        "Load_FactSales",
        "Master_Nightly",
        "Stage_Customers",
        "Stage_Orders",
    }


def test_scheduled_and_called():
    a = by_name()
    assert a["Master_Nightly"].scheduled
    assert a["Stage_Orders"].called_by == ["Master_Nightly"]
    assert scheduled_packages(ESTATE.parent / "missing.yml") == set()


def test_effort_totals():
    a = by_name()
    assert sum(x.effort for x in a.values()) == 80
    assert a["Load_DimCustomer"].complexity == "high"
    assert a["Stage_Customers"].complexity == "low"


def test_semantics_flagged(packages):
    items = assess_package(packages["Load_FactSales"]).items
    assert any(i.ssis == "Microsoft.Lookup" and i.semantics for i in items)


def test_unknown_task_gets_default_effort(packages):
    p = packages["Stage_Customers"]
    t = next(p.all_tasks())
    old = t.kind
    t.kind = "Vendor.CustomTask"
    try:
        item = next(i for i in assess_package(p).items if i.ssis == "Vendor.CustomTask")
        assert item.effort == 5 and item.target == "not in patterns.yml"
    finally:
        t.kind = old


def test_markdown_summary_line():
    text = markdown(assess_project(ESTATE))
    assert "8 packages, 80 points." in text
