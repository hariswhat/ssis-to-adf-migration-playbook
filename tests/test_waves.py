from playbook.waves import lineage, plan


def test_waves(packages):
    waves, _ = plan(list(packages.values()))
    assert waves == [
        ["Import_DealerTargets", "Stage_Customers", "Stage_Orders"],
        ["Load_DimCustomer"],
        ["Load_FactSales", "Maintenance_Purge"],
        ["Export_PlantExtract"],
        ["Master_Nightly"],
    ]


def test_lineage_from_variable_expression(packages):
    lin = lineage(packages["Stage_Orders"])
    assert "ERP:dbo.order_line" in lin.reads
    assert "DW:stg.order_line" in lin.writes


def test_delete_target_is_not_a_read(packages):
    lin = lineage(packages["Maintenance_Purge"])
    assert lin.writes and not (lin.writes & lin.reads)


def test_script_task_is_opaque(packages):
    assert lineage(packages["Export_PlantExtract"]).opaque == ["Build extract file"]
