import asyncio

import pytest

pytest.importorskip("mcp")

from sqlscout.mcp_server import build_server  # noqa: E402


def test_server_exposes_three_tools(toolbox):
    server = build_server(toolbox)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {"list_tables", "describe_table", "run_sql"}


def test_run_sql_tool_goes_through_the_guard(toolbox):
    server = build_server(toolbox)
    ok = asyncio.run(server.call_tool("run_sql", {"query": "SELECT COUNT(*) AS n FROM orders"}))
    bad = asyncio.run(server.call_tool("run_sql", {"query": "DROP TABLE orders"}))
    assert "12000" in str(ok) and "Rejected" in str(bad)
