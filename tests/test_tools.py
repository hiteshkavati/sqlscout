import json


def test_list_tables(toolbox):
    out = toolbox.call("list_tables", {})
    assert not out.is_error and "orders" in out.content and "customers" in out.content


def test_describe_table_hides_restricted_column(toolbox):
    out = toolbox.call("describe_table", {"table": "customers"})
    assert "segment" in out.content and "email" not in out.content


def test_describe_unknown_table(toolbox):
    out = toolbox.call("describe_table", {"table": "nope"})
    assert out.is_error and "Available" in out.content


def test_run_sql_returns_json_and_result(toolbox):
    out = toolbox.call("run_sql", {"query": "SELECT status, COUNT(*) n FROM orders GROUP BY 1"})
    payload = json.loads(out.content)
    assert payload["columns"] == ["status", "n"] and payload["row_count"] == 4
    assert out.result is not None and out.sql


def test_preview_is_capped_but_result_is_complete(toolbox):
    out = toolbox.call("run_sql", {"query": "SELECT * FROM orders LIMIT 150"})
    payload = json.loads(out.content)
    assert len(payload["rows"]) == 40 and payload["row_count"] == 150
    assert len(out.result.rows) == 150


def test_blocked_query_is_flagged_and_explained(toolbox):
    out = toolbox.call("run_sql", {"query": "DROP TABLE orders"})
    assert out.is_error and out.blocked == "unsafe" and out.result is None


def test_invalid_query_is_flagged_as_invalid(toolbox):
    out = toolbox.call("run_sql", {"query": "SELECT nope FROM orders"})
    assert out.is_error and out.blocked == "invalid"


def test_unknown_tool_and_bad_arguments(toolbox):
    assert toolbox.call("nope", {}).is_error
    assert toolbox.call("run_sql", "not a dict").is_error
    assert toolbox.call("run_sql", {}).is_error  # empty query
