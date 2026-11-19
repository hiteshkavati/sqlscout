"""The tools the agent can call."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .db import Database, DatabaseError, QueryResult
from .guard import SQLRejected, validate
from .schema import Catalog

PREVIEW_ROWS = 40  # rows echoed back to the model; the full capped result stays with the caller

TOOL_SPECS = [
    {
        "name": "list_tables",
        "description": "List the tables available for querying, with row counts.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "describe_table",
        "description": "Show a table's columns, types, descriptions and example values.",
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "Table name"}},
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SQLite SELECT query and return the rows. Only the tables and "
            "columns shown in the schema are available. Results are capped."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A single SELECT query"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    blocked: str | None = None  # SQLRejected kind when the guard refused the query
    sql: str | None = None  # checked SQL that actually ran
    result: QueryResult | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


class ToolBox:
    def __init__(self, db: Database, catalog: Catalog):
        self.db = db
        self.catalog = catalog
        self.policy = db.policy

    @property
    def specs(self) -> list[dict]:
        return TOOL_SPECS

    def call(self, name: str, arguments: dict) -> ToolOutput:
        if not isinstance(arguments, dict):
            return ToolOutput("Arguments must be an object.", True)
        if name == "list_tables":
            return self._list_tables()
        if name == "describe_table":
            return self._describe(str(arguments.get("table", "")))
        if name == "run_sql":
            return self._run_sql(str(arguments.get("query", "")))
        return ToolOutput(f"Unknown tool '{name}'.", True)

    def _list_tables(self) -> ToolOutput:
        lines = [
            f"{t.name} ({t.rows} rows): {t.description}".rstrip(": ") for t in self.catalog.tables
        ]
        return ToolOutput("\n".join(lines))

    def _describe(self, table: str) -> ToolOutput:
        t = self.catalog.table(table)
        if t is None:
            names = ", ".join(x.name for x in self.catalog.tables)
            return ToolOutput(f"No table '{table}'. Available: {names}.", True)
        lines = [f"{t.name} ({t.rows} rows): {t.description}"]
        for c in t.columns:
            extra = f" - {c.description}" if c.description else ""
            vals = f" Values: {', '.join(c.samples)}." if c.samples else ""
            lines.append(f"  {c.name} {c.type}{extra}{vals}")
        return ToolOutput("\n".join(lines))

    def _run_sql(self, query: str) -> ToolOutput:
        try:
            checked = validate(query, self.policy)
        except SQLRejected as e:
            prefix = "Rejected" if e.kind != "invalid" else "Invalid query"
            return ToolOutput(f"{prefix}: {e.message}", True, blocked=e.kind)
        try:
            result = self.db.execute(checked.sql)
        except DatabaseError as e:
            return ToolOutput(str(e), True, sql=checked.sql, notes=checked.notes)
        shown = result.rows[:PREVIEW_ROWS]
        payload = {
            "columns": list(result.columns),
            "rows": [list(r) for r in shown],
            "row_count": len(result.rows),
            "truncated": result.truncated,
        }
        if len(result.rows) > len(shown):
            payload["note"] = f"Showing the first {len(shown)} of {len(result.rows)} rows."
        if checked.notes:
            payload["guard"] = list(checked.notes)
        return ToolOutput(
            json.dumps(payload, default=str), sql=checked.sql, result=result, notes=checked.notes
        )
