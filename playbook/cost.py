"""Count what one nightly run of the converted factory is billed for.

ADF bills activity runs (per 1,000, priced by the integration runtime they
run on), activity execution time (data movement by DIU-hour on the Azure IR,
by hour on a self-hosted IR) and Azure-SSIS IR node hours while it is
running. This walks adf/pipeline from pl_master_nightly and counts the first
of these exactly; time-based meters depend on run durations nobody has
measured yet, so they are inputs.

Prices are not stored here: they change and vary by region. Pass them from
the current pricing page to get a monthly figure (docs/cost-estimate.md).

    python -m playbook.cost --files 3
    python -m playbook.cost --files 3 --price-azure-run 1.0 --price-shir-run 1.5 \\
        --ssis-hours 1 --price-ssis-node-hour <price>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ADF = Path(__file__).resolve().parents[1] / "adf"


def _load(kind: str) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in (ADF / kind).glob("*.json")}


class RunCounter:
    def __init__(self, files: int):
        self.files = files
        self.pipelines = _load("pipeline")
        self.datasets = _load("dataset")
        self.linked = _load("linkedService")
        self.runs: Counter[str] = Counter()

    def _ir_of_linked(self, name: str) -> str:
        via = self.linked[name]["properties"].get("connectVia")
        return "self-hosted" if via and via["referenceName"] == "ir-selfhosted" else "azure"

    def _ir_of_dataset(self, name: str) -> str:
        return self._ir_of_linked(self.datasets[name]["properties"]["linkedServiceName"]["referenceName"])

    def ir(self, a: dict) -> str:
        """Where the activity runs. A copy runs on the self-hosted IR if either
        end needs it; anything else follows its linked service or dataset."""
        if a["type"] == "ExecuteSSISPackage":
            return "azure"  # the orchestration run; the package itself is on the SSIS IR
        refs = [d["referenceName"] for d in a.get("inputs", []) + a.get("outputs", [])]
        tp = a.get("typeProperties", {})
        if "dataset" in tp:
            refs.append(tp["dataset"]["referenceName"])
        irs = {self._ir_of_dataset(r) for r in refs}
        if "linkedServiceName" in a:
            irs.add(self._ir_of_linked(a["linkedServiceName"]["referenceName"]))
        return "self-hosted" if "self-hosted" in irs else "azure"

    def walk(self, acts: list[dict], times: int = 1) -> None:
        for a in acts:
            deps = [c for d in a["dependsOn"] for c in d["dependencyConditions"]]
            if deps and all(c == "Failed" for c in deps):
                continue  # failure alerts: not run on a good night
            self.runs[f"{self.ir(a)} {a['type']}"] += times
            tp = a.get("typeProperties", {})
            if a["type"] == "ExecutePipeline":
                self.walk(self.pipelines[tp["pipeline"]["referenceName"]]["properties"]["activities"], times)
            elif a["type"] == "ForEach":
                self.walk(tp["activities"], times * self.files)
            elif a["type"] == "IfCondition":
                self.walk(tp.get("ifTrueActivities", []), times)

    def nightly(self, root: str = "pl_master_nightly") -> Counter[str]:
        self.runs.clear()
        self.walk(self.pipelines[root]["properties"]["activities"])
        return self.runs


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("--files", type=int, default=1, help="dealer files per night")
    p.add_argument("--nights", type=int, default=30)
    p.add_argument("--price-azure-run", type=float, help="per 1,000 activity runs, Azure IR")
    p.add_argument("--price-shir-run", type=float, help="per 1,000 activity runs, self-hosted IR")
    p.add_argument("--ssis-hours", type=float, help="Azure-SSIS IR running hours per night")
    p.add_argument("--ssis-nodes", type=int, default=1)
    p.add_argument("--price-ssis-node-hour", type=float)
    a = p.parse_args()
    runs = RunCounter(a.files).nightly()
    for k, v in sorted(runs.items()):
        print(f"{k:<40}{v:>4}")
    azure = sum(v for k, v in runs.items() if k.startswith("azure"))
    shir = sum(v for k, v in runs.items() if k.startswith("self-hosted"))
    print(f"\nActivity runs per night: {azure} on the Azure IR, {shir} on the self-hosted IR")
    print(f"Per {a.nights} nights: {azure * a.nights:,} and {shir * a.nights:,}")
    if a.price_azure_run is not None and a.price_shir_run is not None:
        cost = (azure * a.price_azure_run + shir * a.price_shir_run) * a.nights / 1000
        print(f"Orchestration: {cost:,.2f} per {a.nights} nights")
    if a.ssis_hours is not None and a.price_ssis_node_hour is not None:
        cost = a.ssis_hours * a.ssis_nodes * a.price_ssis_node_hour * a.nights
        print(f"Azure-SSIS IR: {cost:,.2f} per {a.nights} nights")
    return 0


if __name__ == "__main__":
    sys.exit(main())
