"""Expose the guarded tools over the Model Context Protocol (stdio).

Any MCP client can then list tables, describe them and run read-only SQL through the same
guard, limits and database protections the agent uses.
"""

from __future__ import annotations

from .config import Settings
from .tools import ToolBox

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError:  # pragma: no cover
        _Server = None


def build_server(tools: ToolBox):
    if _Server is None:
        raise RuntimeError('The MCP package is not installed. Run: pip install "sqlscout[mcp]"')
    server = _Server("sqlscout")

    @server.tool()
    def list_tables() -> str:
        """List the tables available for querying, with row counts."""
        return tools.call("list_tables", {}).content

    @server.tool()
    def describe_table(table: str) -> str:
        """Show a table's columns, types, descriptions and example values."""
        return tools.call("describe_table", {"table": table}).content

    @server.tool()
    def run_sql(query: str) -> str:
        """Run one read-only SQLite SELECT query. Results are capped; the guard rejects
        anything that is not a plain SELECT over the available tables and columns."""
        return tools.call("run_sql", {"query": query}).content

    return server


def serve(settings: Settings) -> None:
    from .bootstrap import build_toolbox

    build_server(build_toolbox(settings)).run()
