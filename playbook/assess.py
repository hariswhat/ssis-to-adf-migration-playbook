"""Score each package for migration: what every task and component becomes in
Azure Data Factory, how much work it is in points, what blocks a mechanical
rewrite, and which behaviour differences reconciliation has to look for.

Rules live in patterns.yml so a team can change them without touching code.

    python -m playbook.assess estate/NorthgateETL           # markdown to stdout
    python -m playbook.assess estate/NorthgateETL --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .dtsx import Package, load_project

PATTERNS = yaml.safe_load((Path(__file__).with_name("patterns.yml")).read_text())

REWRITE = "Rewrite to ADF"
LIFT = "Run on Azure-SSIS IR, rewrite later"
CHECK = "Check whether still needed"


@dataclass
class Item:
    package: str
    where: str
    ssis: str
    target: str
    effort: int
    blocker: bool = False
    semantics: str | None = None
    note: str | None = None


@dataclass
class Assessment:
    package: str
    items: list[Item] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    scheduled: bool = False

    @property
    def effort(self) -> int:
        return sum(i.effort for i in self.items)

    @property
    def blockers(self) -> list[Item]:
        return [i for i in self.items if i.blocker]

    @property
    def complexity(self) -> str:
        b = PATTERNS["bands"]
        return "low" if self.effort <= b["low"] else "medium" if self.effort <= b["medium"] else "high"

    @property
    def path(self) -> str:
        if self.blockers:
            return LIFT
        if not self.called_by and not self.scheduled:
            return CHECK
        return REWRITE


def _rule(section: str, key: str) -> dict:
    return PATTERNS[section].get(key) or {
        "target": "not in patterns.yml",
        "effort": PATTERNS["unknown_effort"],
        "note": "unknown; review by hand",
    }


def assess_package(pkg: Package) -> Assessment:
    a = Assessment(pkg.name)
    for t in pkg.all_tasks():
        r = _rule("tasks", t.kind)
        if t.in_event_handler:
            a.items.append(
                Item(
                    pkg.name,
                    f"{t.in_event_handler} handler: {t.name}",
                    t.kind,
                    f"Failure path: {r['target']}",
                    PATTERNS["expressions"]["event_handler"],
                )
            )
            continue
        a.items.append(
            Item(
                pkg.name,
                t.name,
                t.kind,
                r["target"],
                r["effort"],
                r.get("blocker", False),
                r.get("semantics"),
                r.get("note"),
            )
        )
        for c in t.components:
            cr = _rule("components", c.class_id)
            a.items.append(
                Item(
                    pkg.name,
                    f"{t.name} / {c.name}",
                    c.class_id,
                    cr["target"],
                    cr["effort"],
                    cr.get("blocker", False),
                    cr.get("semantics"),
                    cr.get("note"),
                )
            )
    for c in pkg.connections:
        if c.expression:
            a.items.append(
                Item(
                    pkg.name,
                    f"connection {c.name}",
                    "PropertyExpression",
                    "Linked service parameter",
                    PATTERNS["expressions"]["connection"],
                )
            )
    for v in pkg.variables:
        if v.expression:
            a.items.append(
                Item(
                    pkg.name,
                    f"variable {v.name}",
                    "Variable expression",
                    "Pipeline expression (@concat, variables())",
                    PATTERNS["expressions"]["variable"],
                )
            )
    for pc in pkg.constraints:
        if pc.expression:
            a.items.append(
                Item(
                    pkg.name,
                    f"{pc.source} -> {pc.target}",
                    "Constraint expression",
                    "If Condition activity",
                    PATTERNS["expressions"]["constraint"],
                )
            )
    return a


def callers(packages: list[Package]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in packages:
        for t in p.all_tasks():
            if t.kind == "Microsoft.ExecutePackageTask":
                child = t.properties.get("PackageName", "").removesuffix(".dtsx")
                out.setdefault(child, []).append(p.name)
    return out


def scheduled_packages(jobs_file: Path) -> set[str]:
    """Package names run directly by SQL Agent jobs (estate/agent_jobs.yml)."""
    if not jobs_file.exists():
        return set()
    doc = yaml.safe_load(jobs_file.read_text()) or {}
    return {
        Path(step["package"]).stem for job in doc.get("jobs", []) for step in job.get("steps", []) if "package" in step
    }


def assess_project(folder: Path, jobs_file: Path | None = None) -> list[Assessment]:
    packages = load_project(folder)
    calls = callers(packages)
    scheduled = scheduled_packages(jobs_file or folder.parent / "agent_jobs.yml")
    out = []
    for p in packages:
        a = assess_package(p)
        a.called_by = calls.get(p.name, [])
        a.scheduled = p.name in scheduled
        out.append(a)
    return out


def markdown(assessments: list[Assessment]) -> str:
    lines = [
        "| Package | Path | Complexity | Points | Blockers | Behaviour to reconcile |",
        "|---|---|---|---|---|---|",
    ]
    for a in assessments:
        sem = sorted({i.ssis.removeprefix("Microsoft.") for i in a.items if i.semantics})
        lines.append(
            f"| {a.package} | {a.path} | {a.complexity} | {a.effort} | "
            f"{', '.join(i.where for i in a.blockers) or '-'} | {', '.join(sem) or '-'} |"
        )
    total = sum(a.effort for a in assessments)
    by_path: dict[str, int] = {}
    for a in assessments:
        by_path[a.path] = by_path.get(a.path, 0) + 1
    lines += [
        "",
        f"{len(assessments)} packages, {total} points. " + "; ".join(f"{k}: {v}" for k, v in sorted(by_path.items())),
    ]
    return "\n".join(lines)


def write_csv(assessments: list[Assessment], path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["package", "where", "ssis", "target", "effort", "blocker", "semantics", "note"])
        for a in assessments:
            for i in a.items:
                w.writerow([i.package, i.where, i.ssis, i.target, i.effort, i.blocker, i.semantics or "", i.note or ""])


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--csv", type=Path)
    a = p.parse_args()
    result = assess_project(a.folder)
    print(markdown(result))
    if a.csv:
        write_csv(result, a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
