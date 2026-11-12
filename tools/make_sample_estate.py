"""Write the sample SSIS project this playbook migrates: estate/NorthgateETL.

Northgate's nightly ETL (fictional) as eight SSIS 2016 packages that use the
patterns migrations usually meet: Execute SQL, simple copies, a watermark
load with a parameterised source query, the Slowly Changing Dimension wizard
with an OLE DB Command, lookups with full cache, a ForEach file loop, a Script
Task with an FTP upload, Execute Package, and an OnError e-mail handler.

The XML follows the SSIS 2016 .dtsx layout (package format version 8) closely
enough for the playbook's parser and for a reader who knows SSIS, but it was
written by this script, not saved by Visual Studio, and has not been opened in
SSDT. GUIDs are derived from names so the output is stable.

    python -m tools.make_sample_estate
"""

from __future__ import annotations

import uuid
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "estate" / "NorthgateETL"
NS = uuid.UUID("2b7e4f10-5c3d-4a8e-9f61-0d2c3b4a5e6f")


def gid(*parts: str) -> str:
    return "{" + str(uuid.uuid5(NS, "/".join(parts))).upper() + "}"


def q(s: str) -> str:
    return escape(s, {'"': "&quot;"})


CONNECTIONS = {
    "ERP": (
        "OLEDB",
        "Data Source=ERPSQL01;Initial Catalog=ERP;Provider=SQLNCLI11.1;Integrated Security=SSPI;",
        "@[$Project::ERP_ConnectionString]",
    ),
    "DW": (
        "OLEDB",
        "Data Source=DWSQL01;Initial Catalog=NorthgateDW;Provider=SQLNCLI11.1;Integrated Security=SSPI;",
        "@[$Project::DW_ConnectionString]",
    ),
    "DealerFile": ("FLATFILE", r"\\fileshare01\dealers\inbound\dealer_targets.csv", "@[User::CurrentFile]"),
    "SMTP": ("SMTP", "SmtpServer=smtp.northgate.example;UseWindowsAuthentication=True;", None),
    "PlantFTP": ("FTP", "ftp.plantpartner.example:21", None),
}


def conn_managers(names: list[str]) -> str:
    out = ["  <DTS:ConnectionManagers>"]
    for n in names:
        kind, cs, expr = CONNECTIONS[n]
        out.append(
            f'    <DTS:ConnectionManager DTS:refId="Package.ConnectionManagers[{n}]" '
            f'DTS:CreationName="{kind}" DTS:DTSID="{gid("cm", n)}" DTS:ObjectName="{n}">'
        )
        if expr:
            out.append(f'      <DTS:PropertyExpression DTS:Name="ConnectionString">{q(expr)}</DTS:PropertyExpression>')
        out.append("      <DTS:ObjectData>")
        out.append(f'        <DTS:ConnectionManager DTS:ConnectionString="{q(cs)}" />')
        out.append("      </DTS:ObjectData>")
        out.append("    </DTS:ConnectionManager>")
    out.append("  </DTS:ConnectionManagers>")
    return "\n".join(out)


def variables(pkg: str, vars_: list[tuple[str, int, str]], expressions: dict[str, str] | None = None) -> str:
    if not vars_:
        return ""
    out = ["  <DTS:Variables>"]
    for name, dtype, value in vars_:
        expr = (expressions or {}).get(name)
        attrs = f' DTS:EvaluateAsExpression="True" DTS:Expression="{q(expr)}"' if expr else ""
        out.append(
            f'    <DTS:Variable DTS:CreationName="" DTS:DTSID="{gid(pkg, "var", name)}" '
            f'DTS:IncludeInDebugDump="2345" DTS:Namespace="User" DTS:ObjectName="{name}"{attrs}>'
        )
        out.append(f'      <DTS:VariableValue DTS:DataType="{dtype}">{q(value)}</DTS:VariableValue>')
        out.append("    </DTS:Variable>")
    out.append("  </DTS:Variables>")
    return "\n".join(out)


def task(path: str, name: str, kind: str, body: str, indent: int = 4, children: str = "") -> str:
    p = " " * indent
    ex = kind if not kind.startswith("STOCK:") else kind
    return (
        f'{p}<DTS:Executable DTS:refId="{path}\\{name}" DTS:CreationName="{kind}" '
        f'DTS:DTSID="{gid(path, name)}" DTS:ExecutableType="{ex}" DTS:ObjectName="{q(name)}">\n'
        + (children + "\n" if children else "")
        + f"{p}  <DTS:ObjectData>\n{body}\n{p}  </DTS:ObjectData>\n{p}</DTS:Executable>"
    )


def sql_task(path: str, name: str, conn: str, sql: str, result_var: str | None = None) -> str:
    rs = ' SQLTask:ResultType="ResultSetType_SingleRow"' if result_var else ""
    binding = (
        f'\n          <SQLTask:ResultBinding SQLTask:ResultName="0" SQLTask:DtsVariableName="User::{result_var}" />'
        if result_var
        else ""
    )
    body = (
        f'        <SQLTask:SqlTaskData SQLTask:Connection="{gid("cm", conn)}" '
        f'SQLTask:SqlStatementSource="{q(sql)}"{rs} '
        f'xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask">{binding}\n'
        f"        </SQLTask:SqlTaskData>"
    )
    return task(path, name, "Microsoft.ExecuteSQLTask", body)


def component(df: str, name: str, cls: str, props: dict[str, str], conn: str | None = None, extra: str = "") -> str:
    lines = [
        f'            <component refId="{df}\\{name}" componentClassID="{cls}" name="{q(name)}">',
        "              <properties>",
    ]
    for k, v in props.items():
        lines.append(f'                <property name="{k}">{q(v)}</property>')
    lines.append("              </properties>")
    if conn:
        lines.append("              <connections>")
        lines.append(
            f'                <connection refId="{df}\\{name}.Connections[OleDbConnection]" '
            f'connectionManagerID="Package.ConnectionManagers[{conn}]" '
            f'connectionManagerRefId="Package.ConnectionManagers[{conn}]" name="OleDbConnection" />'
        )
        lines.append("              </connections>")
    if extra:
        lines.append(extra)
    lines.append("            </component>")
    return "\n".join(lines)


def data_flow(path: str, name: str, comps: list[str], paths: list[tuple[str, str]]) -> str:
    df = f"{path}\\{name}"
    p_lines = [
        f'            <path refId="{df}.Paths[{a} to {b}]" startId="{df}\\{a}.Outputs[Output]" '
        f'endId="{df}\\{b}.Inputs[Input]" name="{q(a)} to {q(b)}" />'
        for a, b in paths
    ]
    body = (
        '        <pipeline version="1">\n          <components>\n'
        + "\n".join(comps)
        + "\n          </components>\n          <paths>\n"
        + "\n".join(p_lines)
        + "\n          </paths>\n        </pipeline>"
    )
    return task(path, name, "Microsoft.Pipeline", body)


def constraints(pairs: list[tuple[str, str, int, str | None]]) -> str:
    """(from, to, value 0=success 1=failure 2=completion, expression)."""
    if not pairs:
        return ""
    out = ["  <DTS:PrecedenceConstraints>"]
    for i, (a, b, value, expr) in enumerate(pairs):
        ev = f' DTS:EvalOp="3" DTS:Expression="{q(expr)}"' if expr else ""
        out.append(
            f'    <DTS:PrecedenceConstraint DTS:refId="Package.PrecedenceConstraints[Constraint {i}]" '
            f'DTS:CreationName="" DTS:DTSID="{gid("pc", a, b)}" DTS:From="Package\\{a}" '
            f'DTS:To="Package\\{b}" DTS:LogicalAnd="True" DTS:ObjectName="Constraint {i}" '
            f'DTS:Value="{value}"{ev} />'
        )
    out.append("  </DTS:PrecedenceConstraints>")
    return "\n".join(out)


def on_error_mail(pkg: str) -> str:
    body = (
        f'        <SendMailTask:SendMailTaskData SendMailTask:SMTPServer="{gid("cm", "SMTP")}" '
        'SendMailTask:From="etl@northgate.example" SendMailTask:To="dataops@northgate.example" '
        f'SendMailTask:Subject="{q(pkg)} failed" '
        'xmlns:SendMailTask="www.microsoft.com/sqlserver/dts/tasks/sendmailtask" />'
    )
    inner = task("Package.EventHandlers[OnError]", "Mail on error", "Microsoft.SendMailTask", body, 6)
    return (
        "  <DTS:EventHandlers>\n"
        f'    <DTS:EventHandler DTS:refId="Package.EventHandlers[OnError]" DTS:CreationName="OnError" '
        f'DTS:DTSID="{gid(pkg, "onerror")}" DTS:EventName="OnError">\n'
        f"      <DTS:Executables>\n{inner}\n      </DTS:Executables>\n"
        "    </DTS:EventHandler>\n  </DTS:EventHandlers>"
    )


def package(
    name: str,
    conns: list[str],
    body: str,
    vars_: list | None = None,
    pcs: list | None = None,
    on_error: bool = False,
    var_expr: dict | None = None,
) -> str:
    parts = [
        '<?xml version="1.0"?>',
        f'<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package" '
        f'DTS:CreationName="Microsoft.Package" DTS:DTSID="{gid(name)}" '
        f'DTS:ExecutableType="Microsoft.Package" DTS:LastModifiedProductVersion="13.0.5893.48" '
        f'DTS:LocaleID="1033" DTS:ObjectName="{name}" DTS:PackageType="5" '
        f'DTS:VersionBuild="1" DTS:VersionGUID="{gid(name, "version")}">',
        '  <DTS:Property DTS:Name="PackageFormatVersion">8</DTS:Property>',
        conn_managers(conns),
        variables(name, vars_ or [], var_expr),
        f"  <DTS:Executables>\n{body}\n  </DTS:Executables>",
        constraints(pcs or []),
        on_error_mail(name) if on_error else "",
        "</DTS:Executable>",
    ]
    return "\n".join(p for p in parts if p) + "\n"


def build() -> dict[str, str]:
    P = "Package"
    pk: dict[str, str] = {}

    pk["Stage_Customers"] = package(
        "Stage_Customers",
        ["ERP", "DW"],
        "\n".join(
            [
                sql_task(P, "Truncate stage", "DW", "TRUNCATE TABLE stg.customer;"),
                data_flow(
                    P,
                    "Copy customers",
                    [
                        component(
                            f"{P}\\Copy customers",
                            "ERP customers",
                            "Microsoft.OLEDBSource",
                            {
                                "AccessMode": "2",
                                "SqlCommand": "SELECT customer_code, customer_name, region, tier, modified_at FROM dbo.customer",
                            },
                            "ERP",
                        ),
                        component(
                            f"{P}\\Copy customers",
                            "stg customer",
                            "Microsoft.OLEDBDestination",
                            {"AccessMode": "3", "OpenRowset": "[stg].[customer]", "FastLoadOptions": "TABLOCK"},
                            "DW",
                        ),
                    ],
                    [("ERP customers", "stg customer")],
                ),
            ]
        ),
        pcs=[("Truncate stage", "Copy customers", 0, None)],
    )

    pk["Stage_Orders"] = package(
        "Stage_Orders",
        ["ERP", "DW"],
        "\n".join(
            [
                sql_task(P, "Truncate stage", "DW", "TRUNCATE TABLE stg.order_line;"),
                sql_task(
                    P,
                    "Get watermark",
                    "DW",
                    "SELECT CONVERT(VARCHAR(23), MAX(watermark_at), 121) AS last_watermark FROM etl.watermark WHERE table_name = 'order_line'",
                    result_var="LastWatermark",
                ),
                data_flow(
                    P,
                    "Copy new order lines",
                    [
                        component(
                            f"{P}\\Copy new order lines",
                            "ERP order lines",
                            "Microsoft.OLEDBSource",
                            {"AccessMode": "3", "SqlCommandVariable": "User::SourceQuery"},
                            "ERP",
                        ),
                        component(
                            f"{P}\\Copy new order lines",
                            "Count rows",
                            "Microsoft.RowCount",
                            {"VariableName": "User::RowsCopied"},
                        ),
                        component(
                            f"{P}\\Copy new order lines",
                            "stg order_line",
                            "Microsoft.OLEDBDestination",
                            {"AccessMode": "3", "OpenRowset": "[stg].[order_line]", "FastLoadOptions": "TABLOCK"},
                            "DW",
                        ),
                    ],
                    [("ERP order lines", "Count rows"), ("Count rows", "stg order_line")],
                ),
                sql_task(
                    P,
                    "Set watermark",
                    "DW",
                    "UPDATE etl.watermark SET watermark_at = (SELECT MAX(modified_at) FROM stg.order_line) WHERE table_name = 'order_line';",
                ),
            ]
        ),
        vars_=[("LastWatermark", 8, "1900-01-01"), ("SourceQuery", 8, ""), ("RowsCopied", 3, "0")],
        var_expr={
            "SourceQuery": '"SELECT order_id, line_number, customer_code, product_code, order_date, quantity, unit_price, modified_at FROM dbo.order_line WHERE modified_at > \'" + @[User::LastWatermark] + "\'"'
        },
        pcs=[
            ("Truncate stage", "Get watermark", 0, None),
            ("Get watermark", "Copy new order lines", 0, None),
            ("Copy new order lines", "Set watermark", 0, "@[User::RowsCopied] > 0"),
        ],
    )

    pk["Load_DimCustomer"] = package(
        "Load_DimCustomer",
        ["DW"],
        data_flow(
            P,
            "SCD customer",
            [
                component(
                    f"{P}\\SCD customer",
                    "stg customer",
                    "Microsoft.OLEDBSource",
                    {"AccessMode": "0", "OpenRowset": "[stg].[customer]"},
                    "DW",
                ),
                component(
                    f"{P}\\SCD customer",
                    "Slowly Changing Dimension",
                    "Microsoft.SCD",
                    {
                        "SqlCommand": "SELECT customer_key, customer_code, customer_name, region, tier, valid_from, valid_to, is_current FROM dw.dim_customer",
                        "CurrentRowWhere": "[is_current] = 1",
                        "HistoricalAttributes": "region,tier",
                        "ChangingAttributes": "customer_name",
                        "BusinessKey": "customer_code",
                        "IncomingRowChangeType": "1",
                    },
                    "DW",
                ),
                component(
                    f"{P}\\SCD customer",
                    "Expire current row",
                    "Microsoft.OLEDBCommand",
                    {
                        "SqlCommand": "UPDATE dw.dim_customer SET valid_to = GETDATE(), is_current = 0 WHERE customer_code = ? AND is_current = 1"
                    },
                    "DW",
                ),
                component(
                    f"{P}\\SCD customer",
                    "Update changing attributes",
                    "Microsoft.OLEDBCommand",
                    {"SqlCommand": "UPDATE dw.dim_customer SET customer_name = ? WHERE customer_code = ?"},
                    "DW",
                ),
                component(
                    f"{P}\\SCD customer",
                    "Insert new versions",
                    "Microsoft.OLEDBDestination",
                    {"AccessMode": "3", "OpenRowset": "[dw].[dim_customer]"},
                    "DW",
                ),
            ],
            [
                ("stg customer", "Slowly Changing Dimension"),
                ("Slowly Changing Dimension", "Expire current row"),
                ("Slowly Changing Dimension", "Update changing attributes"),
                ("Expire current row", "Insert new versions"),
            ],
        ),
        on_error=True,
    )

    pk["Load_FactSales"] = package(
        "Load_FactSales",
        ["DW"],
        data_flow(
            P,
            "Fact sales",
            [
                component(
                    f"{P}\\Fact sales",
                    "stg order_line",
                    "Microsoft.OLEDBSource",
                    {"AccessMode": "0", "OpenRowset": "[stg].[order_line]"},
                    "DW",
                ),
                component(
                    f"{P}\\Fact sales",
                    "Lookup customer",
                    "Microsoft.Lookup",
                    {
                        "SqlCommand": "SELECT customer_key, customer_code FROM dw.dim_customer WHERE is_current = 1",
                        "CacheType": "0",
                        "NoMatchBehavior": "1",
                        "JoinColumns": "customer_code",
                    },
                    "DW",
                ),
                component(
                    f"{P}\\Fact sales",
                    "Lookup product",
                    "Microsoft.Lookup",
                    {
                        "SqlCommand": "SELECT product_key, product_code FROM dw.dim_product",
                        "CacheType": "0",
                        "NoMatchBehavior": "1",
                        "JoinColumns": "product_code",
                    },
                    "DW",
                ),
                component(
                    f"{P}\\Fact sales",
                    "Unknown members",
                    "Microsoft.DerivedColumn",
                    {
                        "Expression": "customer_key = ISNULL(customer_key) ? -1 : customer_key; product_key = ISNULL(product_key) ? -1 : product_key; net_amount = quantity * unit_price"
                    },
                ),
                component(
                    f"{P}\\Fact sales",
                    "Split bad quantities",
                    "Microsoft.ConditionalSplit",
                    {"Expression": "quantity <= 0", "OutputName": "Rejected"},
                ),
                component(
                    f"{P}\\Fact sales",
                    "fact_sales",
                    "Microsoft.OLEDBDestination",
                    {
                        "AccessMode": "3",
                        "OpenRowset": "[dw].[fact_sales]",
                        "FastLoadOptions": "TABLOCK,CHECK_CONSTRAINTS",
                    },
                    "DW",
                ),
                component(
                    f"{P}\\Fact sales",
                    "sales_rejected",
                    "Microsoft.OLEDBDestination",
                    {"AccessMode": "3", "OpenRowset": "[etl].[sales_rejected]"},
                    "DW",
                ),
            ],
            [
                ("stg order_line", "Lookup customer"),
                ("Lookup customer", "Lookup product"),
                ("Lookup product", "Unknown members"),
                ("Unknown members", "Split bad quantities"),
                ("Split bad quantities", "fact_sales"),
                ("Split bad quantities", "sales_rejected"),
            ],
        ),
        on_error=True,
    )

    loop_body = "\n".join(
        [
            data_flow(
                f"{P}\\For each dealer file",
                "Load file",
                [
                    component(
                        f"{P}\\For each dealer file\\Load file",
                        "Dealer CSV",
                        "Microsoft.FlatFileSource",
                        {"FileNameColumnName": "source_file"},
                        "DealerFile",
                    ),
                    component(
                        f"{P}\\For each dealer file\\Load file",
                        "Convert types",
                        "Microsoft.DataConvert",
                        {"Conversions": "target_amount:DT_NUMERIC(18,2); month_start:DT_DBDATE"},
                    ),
                    component(
                        f"{P}\\For each dealer file\\Load file",
                        "stg dealer_target",
                        "Microsoft.OLEDBDestination",
                        {"AccessMode": "3", "OpenRowset": "[stg].[dealer_target]"},
                        "DW",
                    ),
                ],
                [("Dealer CSV", "Convert types"), ("Convert types", "stg dealer_target")],
            ),
            task(
                f"{P}\\For each dealer file",
                "Archive file",
                "Microsoft.FileSystemTask",
                '        <FileSystemData TaskOperationType="MoveFile" TaskSourcePath="User::CurrentFile" '
                'TaskDestinationPath="\\\\fileshare01\\dealers\\archive" />',
            ),
        ]
    )
    foreach = (
        f'    <DTS:Executable DTS:refId="{P}\\For each dealer file" DTS:CreationName="STOCK:FOREACHLOOP" '
        f'DTS:DTSID="{gid(P, "foreach")}" DTS:ExecutableType="STOCK:FOREACHLOOP" DTS:ObjectName="For each dealer file">\n'
        '      <DTS:ForEachEnumerator DTS:CreationName="Microsoft.ForEachFileEnumerator" DTS:ObjectName="File enumerator">\n'
        '        <DTS:ObjectData>\n'
        '          <ForEachFileEnumeratorProperties>\n'
        '            <FEFEProperty Folder="\\\\fileshare01\\dealers\\inbound" />\n'
        '            <FEFEProperty FileSpec="*.csv" />\n'
        '            <FEFEProperty Recurse="false" />\n'
        '          </ForEachFileEnumeratorProperties>\n'
        '        </DTS:ObjectData>\n'
        '      </DTS:ForEachEnumerator>\n'
        '      <DTS:ForEachVariableMappings>\n'
        f'        <DTS:ForEachVariableMapping DTS:CreationName="" DTS:DTSID="{gid(P, "fevm")}" '
        'DTS:ObjectName="Variable mapping" DTS:ValueIndex="0" DTS:VariableName="User::CurrentFile" />\n'
        '      </DTS:ForEachVariableMappings>\n'
        f"      <DTS:Executables>\n{loop_body}\n      </DTS:Executables>\n"
        "    </DTS:Executable>"
    )
    pk["Import_DealerTargets"] = package(
        "Import_DealerTargets",
        ["DW", "DealerFile"],
        "\n".join(
            [
                sql_task(P, "Truncate stage", "DW", "TRUNCATE TABLE stg.dealer_target;"),
                foreach,
                sql_task(P, "Merge targets", "DW", "EXEC etl.usp_merge_dealer_target;"),
            ]
        ),
        vars_=[("CurrentFile", 8, "")],
        pcs=[("Truncate stage", "For each dealer file", 0, None), ("For each dealer file", "Merge targets", 0, None)],
    )

    script = (
        '        <ScriptProject Name="ST_PlantExtract" VSTAMajorVersion="14" VSTAMinorVersion="0" Language="CSharp" '
        'ReadOnlyVariables="User::ExtractDate" ReadWriteVariables="User::ExtractFile">\n'
        '          <ProjectItem Name="ScriptMain.cs" Encoding="UTF8"><![CDATA[\n'
        "public void Main()\n{\n"
        "    // Builds the fixed-width extract the plant partner expects:\n"
        "    // header record, one record per plant per day, trailer with a checksum.\n"
        '    var path = Dts.Variables["User::ExtractFile"].Value.ToString();\n'
        "    // ... 140 lines of string formatting and a custom mod-97 checksum ...\n"
        "    Dts.TaskResult = (int)ScriptResults.Success;\n}\n"
        "]]></ProjectItem>\n        </ScriptProject>"
    )
    pk["Export_PlantExtract"] = package(
        "Export_PlantExtract",
        ["DW", "PlantFTP"],
        "\n".join(
            [
                task(P, "Build extract file", "Microsoft.ScriptTask", script),
                task(
                    P,
                    "Upload to partner",
                    "Microsoft.FtpTask",
                    '        <FtpData Operation="Send" LocalPath="User::ExtractFile" RemotePath="/inbound/northgate" '
                    f'Connection="{gid("cm", "PlantFTP")}" />',
                ),
            ]
        ),
        vars_=[("ExtractDate", 7, "2026-01-01"), ("ExtractFile", 8, "")],
        pcs=[("Build extract file", "Upload to partner", 0, None)],
    )

    pk["Maintenance_Purge"] = package(
        "Maintenance_Purge",
        ["DW"],
        sql_task(
            P,
            "Purge old rejects",
            "DW",
            "DELETE FROM etl.sales_rejected WHERE rejected_at < DATEADD(DAY, -90, GETDATE());",
        ),
    )

    def exec_pkg(name: str, child: str) -> str:
        return task(
            P,
            name,
            "Microsoft.ExecutePackageTask",
            f"        <ExecutePackageTask><UseProjectReference>True</UseProjectReference>"
            f"<PackageName>{child}.dtsx</PackageName></ExecutePackageTask>",
        )

    children = [
        "Stage_Customers",
        "Stage_Orders",
        "Load_DimCustomer",
        "Load_FactSales",
        "Import_DealerTargets",
        "Export_PlantExtract",
    ]
    pk["Master_Nightly"] = package(
        "Master_Nightly",
        ["SMTP"],
        "\n".join(exec_pkg(f"Run {c}", c) for c in children),
        pcs=[
            ("Run Stage_Customers", "Run Load_DimCustomer", 0, None),
            ("Run Stage_Orders", "Run Load_FactSales", 0, None),
            ("Run Load_DimCustomer", "Run Load_FactSales", 0, None),
            ("Run Load_FactSales", "Run Export_PlantExtract", 0, None),
        ],
        on_error=True,
    )
    return pk


PROJECT_PARAMS = """<?xml version="1.0"?>
<SSIS:Parameters xmlns:SSIS="www.microsoft.com/SqlServer/SSIS">
  <SSIS:Parameter SSIS:Name="ERP_ConnectionString">
    <SSIS:Properties>
      <SSIS:Property SSIS:Name="DataType">18</SSIS:Property>
      <SSIS:Property SSIS:Name="Value">Data Source=ERPSQL01;Initial Catalog=ERP;Provider=SQLNCLI11.1;Integrated Security=SSPI;</SSIS:Property>
      <SSIS:Property SSIS:Name="Sensitive">0</SSIS:Property>
    </SSIS:Properties>
  </SSIS:Parameter>
  <SSIS:Parameter SSIS:Name="DW_ConnectionString">
    <SSIS:Properties>
      <SSIS:Property SSIS:Name="DataType">18</SSIS:Property>
      <SSIS:Property SSIS:Name="Value">Data Source=DWSQL01;Initial Catalog=NorthgateDW;Provider=SQLNCLI11.1;Integrated Security=SSPI;</SSIS:Property>
      <SSIS:Property SSIS:Name="Sensitive">0</SSIS:Property>
    </SSIS:Properties>
  </SSIS:Parameter>
</SSIS:Parameters>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, xml in build().items():
        (OUT / f"{name}.dtsx").write_text(xml)
    (OUT / "Project.params").write_text(PROJECT_PARAMS)
    print(f"{len(build())} packages written to {OUT}")


if __name__ == "__main__":
    main()
