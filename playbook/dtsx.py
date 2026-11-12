"""Read SSIS packages (.dtsx, format version 8) into plain Python objects.

Covers what the assessment, wave planner and converter need: connection
managers (with expressions), variables (with expressions), the task tree
including containers and event handlers, precedence constraints, and data
flow components with their properties and paths. Anything else in the file is
ignored, not rejected, so packages from other projects can still be
inventoried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

DTS = "www.microsoft.com/SqlServer/Dts"


def _a(el: ET.Element, name: str, default: str = "") -> str:
    return el.get(f"{{{DTS}}}{name}", default)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class Connection:
    name: str
    kind: str
    dtsid: str
    connection_string: str
    expression: str | None


@dataclass
class Variable:
    name: str
    value: str
    expression: str | None


@dataclass
class Component:
    name: str
    class_id: str
    properties: dict[str, str]
    connection: str | None


@dataclass
class Task:
    name: str
    path: str
    kind: str
    properties: dict[str, str] = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)
    paths: list[tuple[str, str]] = field(default_factory=list)
    children: list[Task] = field(default_factory=list)
    in_event_handler: str | None = None

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Constraint:
    source: str
    target: str
    value: int  # 0 success, 1 failure, 2 completion
    expression: str | None


@dataclass
class Package:
    name: str
    file: Path
    connections: list[Connection]
    variables: list[Variable]
    tasks: list[Task]
    constraints: list[Constraint]

    def all_tasks(self):
        for t in self.tasks:
            yield from t.walk()

    def connection_by_id(self, dtsid: str) -> Connection | None:
        return next((c for c in self.connections if c.dtsid == dtsid), None)


def _task_properties(obj: ET.Element | None) -> dict[str, str]:
    """Flatten the task-specific payload under ObjectData into name -> value.
    Attributes keep their local name; child text keeps the child's tag."""
    props: dict[str, str] = {}
    if obj is None:
        return props
    for el in obj.iter():
        for k, v in el.attrib.items():
            props.setdefault(k.rsplit("}", 1)[-1], v)
        tag = _local(el.tag)
        if el.text and el.text.strip() and tag not in ("ObjectData",):
            props.setdefault(tag, el.text.strip())
    return props


def _components(obj: ET.Element | None) -> tuple[list[Component], list[tuple[str, str]]]:
    comps, paths = [], []
    if obj is None:
        return comps, paths
    pipeline = obj.find("pipeline")
    if pipeline is None:
        return comps, paths
    for c in pipeline.iter("component"):
        props = {p.get("name"): (p.text or "") for p in c.iter("property")}
        conn_el = c.find("connections/connection")
        conn = None
        if conn_el is not None:
            m = re.search(r"ConnectionManagers\[(.+?)\]", conn_el.get("connectionManagerRefId", ""))
            conn = m.group(1) if m else None
        comps.append(Component(c.get("name", ""), c.get("componentClassID", ""), props, conn))
    for p in pipeline.iter("path"):
        start = p.get("startId", "").rsplit("\\", 1)[-1].split(".Outputs")[0]
        end = p.get("endId", "").rsplit("\\", 1)[-1].split(".Inputs")[0]
        paths.append((start, end))
    return comps, paths


def _task(el: ET.Element, handler: str | None = None) -> Task:
    obj = el.find(f"{{{DTS}}}ObjectData")
    kind = _a(el, "ExecutableType") or _a(el, "CreationName")
    t = Task(
        name=_a(el, "ObjectName"),
        path=_a(el, "refId"),
        kind=kind,
        properties=_task_properties(obj),
        in_event_handler=handler,
    )
    if kind == "Microsoft.Pipeline":
        t.components, t.paths = _components(obj)
    enum = el.find(f"{{{DTS}}}ForEachEnumerator")
    if enum is not None:
        t.properties["Enumerator"] = _a(enum, "CreationName")
        for fe in enum.iter("FEFEProperty"):
            t.properties.update(fe.attrib)
    for child in el.findall(f"{{{DTS}}}Executables/{{{DTS}}}Executable"):
        t.children.append(_task(child, handler))
    return t


def parse(path: Path) -> Package:
    root = ET.parse(path).getroot()
    conns = []
    for cm in root.findall(f"{{{DTS}}}ConnectionManagers/{{{DTS}}}ConnectionManager"):
        inner = cm.find(f"{{{DTS}}}ObjectData/{{{DTS}}}ConnectionManager")
        expr = cm.find(f"{{{DTS}}}PropertyExpression[@{{{DTS}}}Name='ConnectionString']")
        conns.append(
            Connection(
                name=_a(cm, "ObjectName"),
                kind=_a(cm, "CreationName"),
                dtsid=_a(cm, "DTSID"),
                connection_string=_a(inner, "ConnectionString") if inner is not None else "",
                expression=expr.text if expr is not None else None,
            )
        )
    variables = []
    for v in root.findall(f"{{{DTS}}}Variables/{{{DTS}}}Variable"):
        val = v.find(f"{{{DTS}}}VariableValue")
        variables.append(
            Variable(
                _a(v, "ObjectName"),
                val.text or "" if val is not None else "",
                _a(v, "Expression") if _a(v, "EvaluateAsExpression") == "True" else None,
            )
        )
    tasks = [_task(e) for e in root.findall(f"{{{DTS}}}Executables/{{{DTS}}}Executable")]
    for eh in root.findall(f"{{{DTS}}}EventHandlers/{{{DTS}}}EventHandler"):
        event = _a(eh, "EventName")
        for e in eh.findall(f"{{{DTS}}}Executables/{{{DTS}}}Executable"):
            tasks.append(_task(e, event))
    constraints = []
    for pc in root.findall(f"{{{DTS}}}PrecedenceConstraints/{{{DTS}}}PrecedenceConstraint"):
        constraints.append(
            Constraint(
                _a(pc, "From").rsplit("\\", 1)[-1],
                _a(pc, "To").rsplit("\\", 1)[-1],
                int(_a(pc, "Value", "0")),
                _a(pc, "Expression") or None,
            )
        )
    return Package(_a(root, "ObjectName") or path.stem, path, conns, variables, tasks, constraints)


def load_project(folder: Path) -> list[Package]:
    return [parse(p) for p in sorted(folder.glob("*.dtsx"))]
