"""Generate Azure Data Factory pipelines from SSIS packages, for the patterns
that convert mechanically, and say plainly what does not.

What it does with each SSIS construct:

  Execute SQL                Script activity; a single-row result set becomes a
                             Lookup activity plus Set Variable
  Data flow, copy-shaped     Copy activity (one source, one destination, with
                             only Row Count and Data Conversion between)
  Data flow, transforming    Stored Procedure activity calling the set-based
                             rewrite named in patterns.yml (rewrites:)
  ForEach file loop          Get Metadata + Filter + ForEach
  File System move           Copy (binary) + Delete
  Execute Package            Execute Pipeline
  Precedence constraint      dependsOn (0 Succeeded, 1 Failed, 2 Completed);
                             with an expression, an If Condition
  OnError Send Mail          one alert Web activity per activity, on failure
  Script Task, FTP upload    the whole package runs unchanged on the
                             Azure-SSIS IR (Execute SSIS Package activity)

SSIS expressions are translated only for the forms that have an exact ADF
equivalent (variables, project parameters, string literals, + concatenation,
a comparison of a Row Count variable with a number). Anything else is
reported as manual work.

    python -m playbook.convert estate/NorthgateETL --out adf
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .assess import PATTERNS, assess_project
from .dtsx import Package, Task, load_project

COPY_SHAPED = {
    "Microsoft.OLEDBSource", "Microsoft.OLEDBDestination", "Microsoft.FlatFileSource",
    "Microsoft.RowCount", "Microsoft.DataConvert",
}
DEPENDENCY = {0: "Succeeded", 1: "Failed", 2: "Completed"}
LINKED = {"ERP": "ls_erp_sqlserver", "DW": "ls_dw_azuresql", "DealerFile": "ls_fileshare"}
SSIS_FOLDER = "NorthgateETL"


@dataclass
class Result:
    package: str
    pipeline: dict
    manual: list[str] = field(default_factory=list)


def pipeline_name(package: str) -> str:
    return "pl_" + re.sub(r"[^a-z0-9]+", "_", package.lower()).strip("_")


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


TOKEN = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|@\[(User|\$Project)::(\w+)\]|(\d+)|(\+|>=|<=|==|!=|>|<))')


def translate(expr: str, row_counts: dict[str, str] | None = None) -> str | None:
    """SSIS expression -> ADF expression body (without the leading @), or None."""
    pos, parts, ops = 0, [], []
    while pos < len(expr.strip()):
        m = TOKEN.match(expr, pos)
        if not m:
            return None
        pos = m.end()
        lit, scope, var, num, op = m.groups()
        if lit is not None:
            parts.append(_lit(lit.replace('\\"', '"')))
        elif var:
            if scope == "User" and row_counts and var in row_counts:
                parts.append(f"activity('{row_counts[var]}').output.rowsCopied")
            elif scope == "User":
                parts.append(f"variables('{var}')")
            else:
                parts.append(f"pipeline().globalParameters.{var}")
        elif num is not None:
            parts.append(num)
        else:
            ops.append(op)
    if ops and all(o == "+" for o in ops) and len(parts) == len(ops) + 1:
        return f"concat({', '.join(parts)})"
    if len(ops) == 1 and len(parts) == 2 and ops[0] != "+":
        fn = {">": "greater", ">=": "greaterOrEquals", "<": "less", "<=": "lessOrEquals",
              "==": "equals"}.get(ops[0])
        return f"{fn}({parts[0]}, {parts[1]})" if fn else f"not(equals({parts[0]}, {parts[1]}))"
    if not ops and len(parts) == 1:
        return parts[0]
    return None


def _dep(names: list[tuple[str, str]]) -> list[dict]:
    return [{"activity": a, "dependencyConditions": [c]} for a, c in names]


def _sql_source(conn: str | None) -> str:
    return "SqlServerSource" if conn == "ERP" else "AzureSqlSource"


class Converter:
    def __init__(self, pkg: Package):
        self.pkg = pkg
        self.manual: list[str] = []
        self.variables: dict[str, dict] = {}
        self.row_counts: dict[str, str] = {}

    # --- tasks -----------------------------------------------------------

    def sql_task(self, t: Task) -> list[dict]:
        sql = t.properties.get("SqlStatementSource", "")
        conn = self.pkg.connection_by_id(t.properties.get("Connection", ""))
        ls = LINKED.get(conn.name if conn else "", "ls_dw_azuresql")
        if t.properties.get("ResultType") == "ResultSetType_SingleRow":
            var = t.properties.get("DtsVariableName", "").removeprefix("User::")
            alias = re.findall(r"\bAS\s+(\w+)", sql, re.I)
            if not alias:
                self.manual.append(f"{t.name}: result column has no alias; ADF reads it by name")
            col = alias[-1] if alias else "UNNAMED"
            self.variables[var] = {"type": "String"}
            return [
                {"name": t.name, "type": "Lookup", "dependsOn": [],
                 "typeProperties": {"source": {"type": _sql_source(conn.name if conn else None),
                                               "sqlReaderQuery": sql},
                                    "dataset": {"referenceName": "ds_dw_query", "type": "DatasetReference"},
                                    "firstRowOnly": True}},
                {"name": f"Set {var}", "type": "SetVariable",
                 "dependsOn": _dep([(t.name, "Succeeded")]),
                 "typeProperties": {"variableName": var,
                                    "value": {"value": f"@string(activity('{t.name}').output.firstRow.{col})",
                                              "type": "Expression"}}},
            ]
        if re.match(r"\s*EXEC(UTE)?\s+", sql, re.I):
            proc = re.match(r"\s*EXEC(?:UTE)?\s+([\w.\[\]]+)", sql, re.I).group(1)
            return [{"name": t.name, "type": "SqlServerStoredProcedure", "dependsOn": [],
                     "linkedServiceName": {"referenceName": ls, "type": "LinkedServiceReference"},
                     "typeProperties": {"storedProcedureName": proc}}]
        return [{"name": t.name, "type": "Script", "dependsOn": [],
                 "linkedServiceName": {"referenceName": ls, "type": "LinkedServiceReference"},
                 "typeProperties": {"scripts": [{"type": "NonQuery", "text": sql}]}}]

    def data_flow(self, t: Task, file_param: str | None = None) -> list[dict]:
        classes = {c.class_id for c in t.components}
        if classes <= COPY_SHAPED:
            src = next(c for c in t.components if "Source" in c.class_id)
            dst = next(c for c in t.components if "Destination" in c.class_id)
            for c in t.components:
                if c.class_id == "Microsoft.RowCount":
                    self.row_counts[c.properties.get("VariableName", "").removeprefix("User::")] = t.name
            if src.class_id == "Microsoft.FlatFileSource":
                source = {"type": "DelimitedTextSource"}
                inputs = [{"referenceName": "ds_fileshare_csv", "type": "DatasetReference",
                           "parameters": {"folder": "@pipeline().parameters.inbound_folder",
                                          "file": file_param or "*.csv"}}]
            else:
                source = {"type": _sql_source(src.connection)}
                var = src.properties.get("SqlCommandVariable", "").removeprefix("User::")
                if var:
                    source["sqlReaderQuery"] = {"value": f"@variables('{var}')", "type": "Expression"}
                elif src.properties.get("SqlCommand"):
                    source["sqlReaderQuery"] = src.properties["SqlCommand"]
                inputs = [{"referenceName": "ds_erp_query" if src.connection == "ERP" else "ds_dw_query",
                           "type": "DatasetReference"}]
            schema, table = dst.properties["OpenRowset"].replace("[", "").replace("]", "").split(".")
            return [{"name": t.name, "type": "Copy", "dependsOn": [],
                     "inputs": inputs,
                     "outputs": [{"referenceName": "ds_dw_table", "type": "DatasetReference",
                                  "parameters": {"schema": schema, "table": table}}],
                     "typeProperties": {"source": source,
                                        "sink": {"type": "AzureSqlSink", "writeBehavior": "insert",
                                                 "tableOption": "none"}}}]
        key = f"{self.pkg.name}/{t.name}"
        proc = PATTERNS.get("rewrites", {}).get(key)
        if not proc:
            proc = f"etl.usp_{re.sub(r'[^a-z0-9]+', '_', key.lower())}"
            self.manual.append(f"{t.name}: no set-based rewrite named in patterns.yml; {proc} is a placeholder")
        return [{"name": t.name, "type": "SqlServerStoredProcedure", "dependsOn": [],
                 "description": f"Set-based rewrite of the SSIS data flow '{t.name}'.",
                 "linkedServiceName": {"referenceName": "ls_dw_azuresql", "type": "LinkedServiceReference"},
                 "typeProperties": {"storedProcedureName": proc}}]

    def foreach_files(self, t: Task) -> list[dict]:
        folder = t.properties.get("Folder", "")
        spec = t.properties.get("FileSpec", "*")
        inner: list[dict] = []
        prev = None
        for child in t.children:
            acts = self.task(child, file_param="@item().name")
            if prev and acts:
                acts[0]["dependsOn"] = _dep([(prev, "Succeeded")])
            inner += acts
            prev = acts[-1]["name"] if acts else prev
        ext = spec.removeprefix("*")
        match = f"@and(equals(item().type, 'File'), endsWith(item().name, {_lit(ext)}))"
        matched = "@activity('Only matching files').output.value"
        return [
            {"name": "List files", "type": "GetMetadata", "dependsOn": [],
             "description": f"SSIS ForEach file enumerator over {folder} ({spec}).",
             "typeProperties": {"dataset": {"referenceName": "ds_fileshare_folder", "type": "DatasetReference",
                                            "parameters": {"folder": "@pipeline().parameters.inbound_folder"}},
                                "fieldList": ["childItems"]}},
            {"name": "Only matching files", "type": "Filter", "dependsOn": _dep([("List files", "Succeeded")]),
             "typeProperties": {"items": {"value": "@activity('List files').output.childItems", "type": "Expression"},
                                "condition": {"value": match, "type": "Expression"}}},
            {"name": t.name, "type": "ForEach", "dependsOn": _dep([("Only matching files", "Succeeded")]),
             "typeProperties": {"items": {"value": matched, "type": "Expression"},
                                "isSequential": True, "activities": inner}},
        ]

    def file_system(self, t: Task, file_param: str | None) -> list[dict]:
        if t.properties.get("TaskOperationType") != "MoveFile":
            self.manual.append(f"{t.name}: File System operation {t.properties.get('TaskOperationType')} not converted")
            return []
        f = file_param or "*"
        return [
            {"name": f"{t.name} copy", "type": "Copy", "dependsOn": [],
             "inputs": [{"referenceName": "ds_fileshare_binary", "type": "DatasetReference",
                         "parameters": {"folder": "@pipeline().parameters.inbound_folder", "file": f}}],
             "outputs": [{"referenceName": "ds_fileshare_binary", "type": "DatasetReference",
                          "parameters": {"folder": "@pipeline().parameters.archive_folder", "file": f}}],
             "typeProperties": {"source": {"type": "BinarySource"}, "sink": {"type": "BinarySink"}}},
            {"name": f"{t.name} delete", "type": "Delete", "dependsOn": _dep([(f"{t.name} copy", "Succeeded")]),
             "typeProperties": {"dataset": {"referenceName": "ds_fileshare_binary", "type": "DatasetReference",
                                            "parameters": {"folder": "@pipeline().parameters.inbound_folder",
                                                           "file": f}},
                                "enableLogging": False}},
        ]

    def task(self, t: Task, file_param: str | None = None) -> list[dict]:
        if t.kind == "Microsoft.ExecuteSQLTask":
            return self.sql_task(t)
        if t.kind == "Microsoft.Pipeline":
            return self.data_flow(t, file_param)
        if t.kind == "STOCK:FOREACHLOOP" and t.properties.get("Enumerator") == "Microsoft.ForEachFileEnumerator":
            return self.foreach_files(t)
        if t.kind == "Microsoft.FileSystemTask":
            return self.file_system(t, file_param)
        if t.kind == "Microsoft.ExecutePackageTask":
            child = t.properties.get("PackageName", "").removesuffix(".dtsx")
            return [{"name": t.name, "type": "ExecutePipeline", "dependsOn": [],
                     "typeProperties": {"pipeline": {"referenceName": pipeline_name(child),
                                                     "type": "PipelineReference"},
                                        "waitOnCompletion": True}}]
        self.manual.append(f"{t.name}: {t.kind} has no conversion")
        return []

    # --- package -----------------------------------------------------------

    def convert(self) -> dict:
        top = [t for t in self.pkg.tasks if not t.in_event_handler]
        by_task: dict[str, list[dict]] = {t.name: self.task(t) for t in top}
        activities: list[dict] = []
        for acts in by_task.values():
            activities += acts
        first = {n: a[0]["name"] for n, a in by_task.items() if a}
        last = {n: a[-1]["name"] for n, a in by_task.items() if a}
        for v in self.pkg.variables:
            self.variables.setdefault(v.name, {"type": "String"})
        # variable expressions become Set Variable activities before their first reader
        for v in self.pkg.variables:
            if not v.expression:
                continue
            body = translate(v.expression, self.row_counts)
            if body is None:
                self.manual.append(f"variable {v.name}: expression not translated: {v.expression}")
                continue
            setter = {"name": f"Set {v.name}", "type": "SetVariable", "dependsOn": [],
                      "typeProperties": {"variableName": v.name,
                                         "value": {"value": f"@{body}", "type": "Expression"}}}
            readers = [a for a in activities if f"variables('{v.name}')" in json.dumps(a)]
            for r in readers:
                r["dependsOn"].append({"activity": setter["name"], "dependencyConditions": ["Succeeded"]})
            sources = re.findall(r"variables\('(\w+)'\)", body)
            setter["dependsOn"] = _dep([(f"Set {s}", "Succeeded") for s in sources
                                        if any(a["name"] == f"Set {s}" for a in activities)])
            activities.append(setter)
        for pc in self.pkg.constraints:
            if pc.source not in last or pc.target not in first:
                continue
            cond = DEPENDENCY[pc.value]
            target = next(a for a in activities if a["name"] == first[pc.target])
            if pc.expression:
                body = translate(pc.expression, self.row_counts)
                if body is None:
                    self.manual.append(f"{pc.source} -> {pc.target}: constraint expression not translated")
                    target["dependsOn"].append({"activity": last[pc.source], "dependencyConditions": [cond]})
                    continue
                wrapped = [a for a in activities if a["name"] in {x["name"] for x in by_task[pc.target]}]
                for a in wrapped:
                    activities.remove(a)
                activities.append({
                    "name": f"If {pc.target}", "type": "IfCondition",
                    "description": f"SSIS precedence constraint expression: {pc.expression}",
                    "dependsOn": _dep([(last[pc.source], cond)]),
                    "typeProperties": {"expression": {"value": f"@{body}", "type": "Expression"},
                                       "ifTrueActivities": wrapped},
                })
            else:
                target["dependsOn"].append({"activity": last[pc.source], "dependencyConditions": [cond]})
        if any(t.in_event_handler == "OnError" for t in self.pkg.tasks):
            for a in list(activities):
                body = (f"@json(concat('{{\"pipeline\": \"', pipeline().Pipeline, '\", "
                        f"\"activity\": \"{a['name']}\", \"run\": \"', pipeline().RunId, '\"}}'))")
                activities.append({
                    "name": f"Alert {a['name']} failed", "type": "WebActivity",
                    "dependsOn": _dep([(a["name"], "Failed")]),
                    "typeProperties": {"method": "POST",
                                       "url": {"value": "@pipeline().globalParameters.alertLogicAppUrl",
                                               "type": "Expression"},
                                       "body": {"value": body, "type": "Expression"}},
                })
        params = {}
        if any(a["type"] == "GetMetadata" for a in activities):
            params = {"inbound_folder": {"type": "string", "defaultValue": "dealers/inbound"},
                      "archive_folder": {"type": "string", "defaultValue": "dealers/archive"}}
        return {"name": pipeline_name(self.pkg.name),
                "properties": {"description": f"Converted from SSIS package {self.pkg.name} by playbook.convert.",
                               "activities": activities, "parameters": params,
                               "variables": self.variables, "annotations": ["converted-from-ssis"]}}


def lift(pkg: Package) -> dict:
    """Packages with blockers run unchanged on the Azure-SSIS IR."""
    return {"name": pipeline_name(pkg.name),
            "properties": {"description": f"Runs SSIS package {pkg.name} unchanged on the Azure-SSIS IR "
                                          "until it is rewritten (docs/adr/0001).",
                           "activities": [{
                               "name": f"Run {pkg.name}", "type": "ExecuteSSISPackage", "dependsOn": [],
                               "policy": {"timeout": "0.02:00:00", "retry": 0},
                               "typeProperties": {
                                   "packageLocation": {"type": "SSISDB",
                                                       "packagePath": f"{SSIS_FOLDER}/{SSIS_FOLDER}/{pkg.name}.dtsx"},
                                   "runtime": "x64", "loggingLevel": "Basic",
                                   "connectVia": {"referenceName": "ir-azure-ssis",
                                                  "type": "IntegrationRuntimeReference"}}}],
                           "annotations": ["azure-ssis-ir"]}}


def convert_project(folder: Path) -> list[Result]:
    assessments = {a.package: a for a in assess_project(folder)}
    out = []
    for pkg in load_project(folder):
        a = assessments[pkg.name]
        if a.blockers:
            out.append(Result(pkg.name, lift(pkg), [f"blocked: {i.where}" for i in a.blockers]))
            continue
        c = Converter(pkg)
        out.append(Result(pkg.name, c.convert(), c.manual))
    return out


def _ls(name: str, kind: str, props: dict, via: str | None = None) -> dict:
    body = {"type": kind, "typeProperties": props}
    if via:
        body["connectVia"] = {"referenceName": via, "type": "IntegrationRuntimeReference"}
    return {"name": name, "properties": body}


def _ds(name: str, ls: str, kind: str, props: dict, params: list[str] | None = None) -> dict:
    body = {"linkedServiceName": {"referenceName": ls, "type": "LinkedServiceReference"},
            "type": kind, "typeProperties": props}
    if params:
        body["parameters"] = {p: {"type": "string"} for p in params}
    return {"name": name, "properties": body}


def support_files() -> dict[str, dict]:
    """Linked services, datasets, runtimes and global parameters the generated
    pipelines reference. Host names are placeholders set per environment by
    the ARM template parameters, as in any ADF CI/CD setup."""
    kv = {"store": {"referenceName": "ls_keyvault", "type": "LinkedServiceReference"}}
    files = {
        "linkedService/ls_keyvault.json": _ls("ls_keyvault", "AzureKeyVault",
                                              {"baseUrl": "https://kv-ngbp-etl-dev.vault.azure.net/"}),
        "linkedService/ls_erp_sqlserver.json": _ls(
            "ls_erp_sqlserver", "SqlServer",
            {"connectionString": "Data Source=ERPSQL01;Initial Catalog=ERP;Integrated Security=False",
             "userName": "svc_adf_erp_reader",
             "password": {"type": "AzureKeyVaultSecret", "secretName": "erp-sql-password", **kv}},
            "ir-selfhosted"),
        "linkedService/ls_dw_azuresql.json": _ls(
            "ls_dw_azuresql", "AzureSqlDatabase",
            {"connectionString": "Data Source=sql-ngbp-dw-dev.database.windows.net;Initial Catalog=NorthgateDW",
             "authenticationType": "SystemAssignedManagedIdentity"}),
        "linkedService/ls_fileshare.json": _ls(
            "ls_fileshare", "FileServer",
            {"host": "\\\\fileshare01\\dealers", "userId": "NORTHGATE\\svc_adf_files",
             "password": {"type": "AzureKeyVaultSecret", "secretName": "fileshare-password", **kv}},
            "ir-selfhosted"),
        "dataset/ds_erp_query.json": _ds("ds_erp_query", "ls_erp_sqlserver", "SqlServerTable", {}),
        "dataset/ds_dw_query.json": _ds("ds_dw_query", "ls_dw_azuresql", "AzureSqlTable", {}),
        "dataset/ds_dw_table.json": _ds("ds_dw_table", "ls_dw_azuresql", "AzureSqlTable",
                                        {"schema": {"value": "@dataset().schema", "type": "Expression"},
                                         "table": {"value": "@dataset().table", "type": "Expression"}},
                                        ["schema", "table"]),
        "dataset/ds_fileshare_csv.json": _ds(
            "ds_fileshare_csv", "ls_fileshare", "DelimitedText",
            {"location": {"type": "FileServerLocation",
                          "folderPath": {"value": "@dataset().folder", "type": "Expression"},
                          "fileName": {"value": "@dataset().file", "type": "Expression"}},
             "columnDelimiter": ",", "firstRowAsHeader": True, "quoteChar": "\""},
            ["folder", "file"]),
        "dataset/ds_fileshare_folder.json": _ds(
            "ds_fileshare_folder", "ls_fileshare", "Binary",
            {"location": {"type": "FileServerLocation",
                          "folderPath": {"value": "@dataset().folder", "type": "Expression"}}},
            ["folder"]),
        "dataset/ds_fileshare_binary.json": _ds(
            "ds_fileshare_binary", "ls_fileshare", "Binary",
            {"location": {"type": "FileServerLocation",
                          "folderPath": {"value": "@dataset().folder", "type": "Expression"},
                          "fileName": {"value": "@dataset().file", "type": "Expression"}}},
            ["folder", "file"]),
        "integrationRuntime/ir-selfhosted.json": {
            "name": "ir-selfhosted", "properties": {"type": "SelfHosted",
                                                    "description": "ERP and file share, on-premises"}},
        "integrationRuntime/ir-azure-ssis.json": {
            "name": "ir-azure-ssis",
            "properties": {
                "type": "Managed",
                "description": "Runs packages that cannot be rewritten yet (docs/adr/0001). "
                               "Billed while running: start before the nightly run, stop after "
                               "(runbook section 3).",
                "typeProperties": {
                    "computeProperties": {"location": "EastUS", "nodeSize": "Standard_D2_v3",
                                          "numberOfNodes": 1, "maxParallelExecutionsPerNode": 2},
                    "ssisProperties": {"edition": "Standard", "licenseType": "LicenseIncluded",
                                       "catalogInfo": {"catalogServerEndpoint": "sql-ngbp-dw-dev.database.windows.net",
                                                       "catalogPricingTier": "S1"}}}}},
        "factory/adf-ngbp-etl-dev.json": {
            "name": "adf-ngbp-etl-dev",
            "properties": {"globalParameters": {
                "alertLogicAppUrl": {"type": "String", "value": "https://logic-ngbp-alerts-dev.example/send"}}}},
    }
    return files


def write(results: list[Result], out: Path) -> None:
    (out / "pipeline").mkdir(parents=True, exist_ok=True)
    for r in results:
        (out / "pipeline" / f"{r.pipeline['name']}.json").write_text(json.dumps(r.pipeline, indent=2) + "\n")
    for rel, body in support_files().items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2) + "\n")


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--out", type=Path, default=Path("adf"))
    a = p.parse_args()
    results = convert_project(a.folder)
    write(results, a.out)
    for r in results:
        n = len(r.pipeline["properties"]["activities"])
        print(f"{r.package:<22} -> {r.pipeline['name']:<26} {n:>3} activities")
        for m in r.manual:
            print(f"{'':<26}manual: {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
