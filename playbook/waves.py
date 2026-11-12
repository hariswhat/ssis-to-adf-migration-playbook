"""Table lineage from the packages, and migration waves from it.

A package that reads a table another package writes has to move in the same
wave or later: while both platforms run in parallel, the new pipeline must
read what the old (or new) producer wrote. Two packages that write the same
table move in the same wave, so a table never has writers on both platforms.
Packages whose reads or writes cannot be seen (a Script Task) go in the last
wave and are listed for a manual check.

Lineage comes from SQL text (Execute SQL tasks, source queries, variable
expressions that build queries, lookups, OLE DB commands) and from the table
names on sources and destinations. It is a regular-expression reading of SQL,
good enough for an inventory, not a parser: dynamic SQL and views are
invisible to it.

    python -m playbook.waves estate/NorthgateETL
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .dtsx import Package, load_project

NAME = r"(\[?\w+\]?\.\[?\w+\]?)"
READ = re.compile(rf"\b(?:FROM|JOIN)\s+{NAME}", re.I)
WRITE = re.compile(rf"\b(?:INSERT\s+INTO|UPDATE|TRUNCATE\s+TABLE|DELETE\s+FROM|MERGE\s+INTO|MERGE)\s+{NAME}", re.I)
EXEC = re.compile(rf"\bEXEC(?:UTE)?\s+{NAME}", re.I)
OPAQUE_TASKS = {"Microsoft.ScriptTask"}
OPAQUE_COMPONENTS = {"Microsoft.ManagedComponentHost"}


def norm(name: str) -> str:
    return name.replace("[", "").replace("]", "").lower()


@dataclass
class Lineage:
    package: str
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    procedures: set[str] = field(default_factory=set)
    opaque: list[str] = field(default_factory=list)


def _sql(lin: Lineage, conn: str | None, sql: str) -> None:
    prefix = f"{conn}:" if conn else ""
    lin.writes |= {prefix + norm(m) for m in WRITE.findall(sql)}
    # "DELETE FROM t" names the target, not an input
    reads_text = re.sub(r"\bDELETE\s+FROM\b", "DELETE", sql, flags=re.I)
    lin.reads |= {prefix + norm(m) for m in READ.findall(reads_text)}
    lin.procedures |= {prefix + norm(m) for m in EXEC.findall(sql)}


def lineage(pkg: Package) -> Lineage:
    lin = Lineage(pkg.name)
    var_sql = {v.name: v.expression for v in pkg.variables if v.expression}
    for t in pkg.all_tasks():
        if t.kind in OPAQUE_TASKS:
            lin.opaque.append(t.name)
        if t.kind == "Microsoft.ExecuteSQLTask":
            conn = pkg.connection_by_id(t.properties.get("Connection", ""))
            _sql(lin, conn.name if conn else None, t.properties.get("SqlStatementSource", ""))
        for c in t.components:
            if c.class_id in OPAQUE_COMPONENTS:
                lin.opaque.append(f"{t.name} / {c.name}")
            table = c.properties.get("OpenRowset")
            if table and "Destination" in c.class_id:
                lin.writes.add(f"{c.connection}:{norm(table)}")
            elif table:
                lin.reads.add(f"{c.connection}:{norm(table)}")
            if c.properties.get("SqlCommand"):
                _sql(lin, c.connection, c.properties["SqlCommand"])
            var = c.properties.get("SqlCommandVariable", "").removeprefix("User::")
            if var in var_sql:
                _sql(lin, c.connection, var_sql[var])
            if c.class_id == "Microsoft.FlatFileSource" and c.connection:
                lin.reads.add(f"file:{c.connection}")
    return lin


def plan(packages: list[Package]) -> tuple[list[list[str]], dict[str, Lineage]]:
    """Waves, earliest first, and the lineage they were built from.
    Orchestrator-only packages (only Execute Package tasks) go last."""
    lins = {p.name: lineage(p) for p in packages}
    orchestrators = {
        p.name for p in packages
        if all(t.kind == "Microsoft.ExecutePackageTask" or t.in_event_handler for t in p.all_tasks())
    }
    workers = [n for n in lins if n not in orchestrators]
    writers: dict[str, set[str]] = {}
    for n in workers:
        for t in lins[n].writes:
            writers.setdefault(t, set()).add(n)

    def deps(n: str) -> set[str]:
        return {w for t in lins[n].reads for w in writers.get(t, set()) if w != n}

    wave: dict[str, int] = {}
    visiting: set[str] = set()

    def level(n: str) -> int:
        if n in wave:
            return wave[n]
        if n in visiting:
            raise ValueError(f"cycle in table dependencies through {n}")
        visiting.add(n)
        w = 1 + max((level(d) for d in deps(n)), default=0)
        visiting.discard(n)
        wave[n] = w
        return w

    for n in workers:
        level(n)
    # same-table writers share a wave: move them up to the latest of them
    changed = True
    while changed:
        changed = False
        for names in writers.values():
            top = max(wave[n] for n in names)
            for n in names:
                if wave[n] != top:
                    wave[n], changed = top, True
    last = max(wave.values(), default=0)
    for n in workers:
        if lins[n].opaque:
            wave[n] = last + 1
    final = max(wave.values(), default=0)
    for n in orchestrators:
        wave[n] = final + 1
    waves = [sorted(n for n, w in wave.items() if w == i) for i in range(1, max(wave.values()) + 1)]
    return [w for w in waves if w], lins


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    a = p.parse_args()
    waves, lins = plan(load_project(a.folder))
    for i, w in enumerate(waves, 1):
        print(f"Wave {i}: {', '.join(w)}")
    for n, lin in lins.items():
        print(f"\n{n}\n  reads:  {', '.join(sorted(lin.reads)) or '-'}")
        print(f"  writes: {', '.join(sorted(lin.writes)) or '-'}")
        if lin.procedures:
            print(f"  calls:  {', '.join(sorted(lin.procedures))}")
        if lin.opaque:
            print(f"  cannot see inside: {', '.join(lin.opaque)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
