from conftest import ESTATE

from tools.make_sample_estate import build


def test_committed_estate_matches_generator():
    for name, xml in build().items():
        assert (ESTATE / f"{name}.dtsx").read_text() == xml, name


def test_all_packages_parse(packages):
    assert len(packages) == 8


def test_connection_expressions(packages):
    conns = {c.name: c for c in packages["Stage_Orders"].connections}
    assert conns["ERP"].expression == "@[$Project::ERP_ConnectionString]"


def test_variable_expression_and_constraint(packages):
    p = packages["Stage_Orders"]
    var = {v.name: v for v in p.variables}
    assert var["SourceQuery"].expression.startswith('"SELECT order_id')
    assert var["RowsCopied"].expression is None
    cons = {(c.source, c.target): c for c in p.constraints}
    assert cons[("Copy new order lines", "Set watermark")].expression == "@[User::RowsCopied] > 0"


def test_containers_and_event_handlers(packages):
    p = packages["Import_DealerTargets"]
    loop = next(t for t in p.tasks if t.kind == "STOCK:FOREACHLOOP")
    assert loop.properties["Enumerator"] == "Microsoft.ForEachFileEnumerator"
    assert [c.name for c in loop.children] == ["Load file", "Archive file"]
    handlers = [t for t in packages["Load_FactSales"].all_tasks() if t.in_event_handler]
    assert handlers and all(t.in_event_handler == "OnError" for t in handlers)


def test_data_flow_components_and_paths(packages):
    flow = next(t for t in packages["Load_FactSales"].all_tasks() if t.kind == "Microsoft.Pipeline")
    kinds = [c.class_id for c in flow.components]
    assert kinds.count("Microsoft.Lookup") == 2
    lookup = next(c for c in flow.components if c.name == "Lookup customer")
    assert lookup.connection == "DW"
    assert ("Lookup customer", "Lookup product") in flow.paths
