from pathlib import Path

import pytest

from playbook.dtsx import load_project

ROOT = Path(__file__).resolve().parents[1]
ESTATE = ROOT / "estate" / "NorthgateETL"


@pytest.fixture(scope="session")
def packages():
    return {p.name: p for p in load_project(ESTATE)}
