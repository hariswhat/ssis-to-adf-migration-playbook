from playbook.cost import RunCounter


def test_nightly_runs_one_file():
    runs = RunCounter(files=1).nightly()
    azure = sum(v for k, v in runs.items() if k.startswith("azure"))
    shir = sum(v for k, v in runs.items() if k.startswith("self-hosted"))
    assert (azure, shir) == (16, 6)


def test_each_file_adds_the_loop_body():
    one, three = RunCounter(1).nightly(), RunCounter(3).nightly()
    assert sum(three.values()) - sum(one.values()) == 2 * 3  # Load file, Archive copy, Archive delete


def test_failure_alerts_and_unscheduled_pipelines_are_not_counted():
    runs = RunCounter(1).nightly()
    assert "azure WebActivity" not in runs and "azure Fail" not in runs
    # only the dealer staging truncate is a Script: the other two became
    # pre-copy scripts, and Maintenance_Purge is not called by the master
    assert runs["azure Script"] == 1


def test_erp_copies_run_on_the_self_hosted_ir():
    runs = RunCounter(1).nightly()
    assert runs["self-hosted Copy"] == 4
