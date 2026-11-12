"""The README's numbers come from these commands; keep them in step."""

from conftest import ESTATE, ROOT

from playbook import reconcile, sample_data
from playbook.assess import assess_project, markdown
from playbook.cost import RunCounter
from playbook.waves import plan

README = (ROOT / "README.md").read_text()


def test_assessment_table_is_current():
    table = markdown(assess_project(ESTATE)).split("\n\n")[0]
    assert table in README


def test_wave_rows_are_current(packages):
    waves, _ = plan(list(packages.values()))
    for i, w in enumerate(waves, 1):
        assert f"| {i} | {', '.join(w)} |" in README


def test_reconciliation_numbers_are_current():
    data = sample_data.generate()
    old, new = reconcile.run_legacy(data), reconcile.run_new(data)
    diffs = reconcile.compare(old, new)
    reconcile.classify(diffs, reconcile.load_accepted())
    table = reconcile.report(old, new, diffs, reconcile.load_accepted()).split("\n\n")[0]
    assert table in README
    assert all(d.accepted_by for d in diffs)
    assert f"The {len(diffs):,} accepted differences" in README


def test_activity_run_counts_are_current():
    runs = RunCounter(1).nightly()
    azure = sum(v for k, v in runs.items() if k.startswith("azure"))
    shir = sum(v for k, v in runs.items() if k.startswith("self-hosted"))
    assert f"{azure} activity runs on the Azure IR and {shir} on the self-hosted IR" in README
