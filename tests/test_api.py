import pytest
from fastapi.testclient import TestClient

from sqlscout.agent import Agent
from sqlscout.api import create_app
from sqlscout.config import Settings
from sqlscout.llm import LLMResponse, NullLLM, ScriptedLLM, ToolCall


def make_client(settings, toolbox, llm=None, **overrides):
    s = settings.model_copy(update=overrides)
    agent = Agent(llm or NullLLM(), toolbox)
    return TestClient(create_app(s, agent, toolbox))


@pytest.fixture()
def client(settings, toolbox):
    return make_client(settings, toolbox)


def test_health_without_model(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["model"] is None and "orders" in body["tables"]


def test_schema_hides_restricted_column(client):
    body = client.get("/api/schema").json()
    customers = next(t for t in body["tables"] if t["name"] == "customers")
    assert "email" not in [c["name"] for c in customers["columns"]]
    assert body["restricted"] == {"customers": ["email"]}


def test_examples(client):
    body = client.get("/api/examples").json()
    assert body["questions"] and body["queries"] and body["attacks"]


def test_sql_endpoint_runs_query(client):
    r = client.post("/api/sql", json={"query": "SELECT COUNT(*) AS n FROM orders"})
    body = r.json()
    assert r.status_code == 200 and body["rows"] == [[12000]] and body["columns"] == ["n"]
    assert body["sql"].startswith("SELECT COUNT(*)")


@pytest.mark.parametrize(
    "query,kind",
    [
        ("DROP TABLE orders", "unsafe"),
        ("SELECT email FROM customers", "policy"),
        ("SELECT * FROM nope", "policy"),
        ("SELEC 1", "invalid"),
    ],
)
def test_sql_endpoint_rejects_with_reason(client, query, kind):
    r = client.post("/api/sql", json={"query": query})
    assert r.status_code == 422 and r.json()["error"]["kind"] == kind


def test_sql_endpoint_validates_body(client):
    assert client.post("/api/sql", json={"query": ""}).status_code == 422
    assert client.post("/api/sql", json={}).status_code == 422


def test_ask_disabled_without_model(client):
    r = client.post("/api/ask", json={"question": "How many orders?"})
    assert r.status_code == 503 and "No model" in r.json()["detail"]


def test_ask_with_model(settings, toolbox):
    llm = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall("c", "run_sql", {"query": "SELECT COUNT(*) AS n FROM orders"})]
            ),
            LLMResponse(text="There are 12,000 orders."),
        ]
    )
    c = make_client(settings, toolbox, llm)
    body = c.post("/api/ask", json={"question": "How many orders?"}).json()
    assert body["status"] == "ok" and body["answer"] == "There are 12,000 orders."
    assert body["rows"] == [[12000]] and body["steps"][0]["tool"] == "run_sql"
    assert c.get("/api/health").json()["model"] == "scripted"


def test_ask_validates_question_length(settings, toolbox):
    c = make_client(settings, toolbox, ScriptedLLM([]))
    assert c.post("/api/ask", json={"question": "hi"}).status_code == 422
    assert c.post("/api/ask", json={"question": "x" * 501}).status_code == 422


def test_api_key_is_enforced_when_set(settings, toolbox):
    c = make_client(settings, toolbox, api_key="secret")
    body = {"query": "SELECT 1"}
    assert c.post("/api/sql", json=body).status_code == 401
    assert c.post("/api/sql", json=body, headers={"x-api-key": "wrong"}).status_code == 401
    assert c.post("/api/sql", json=body, headers={"x-api-key": "secret"}).status_code == 200
    assert c.get("/api/health").status_code == 200


def test_rate_limit(settings, toolbox):
    c = make_client(settings, toolbox, rate_limit_per_min=3)
    codes = [c.post("/api/sql", json={"query": "SELECT 1"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_security_headers_and_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "SQLScout" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_static_assets_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_ui_script_uses_no_innerhtml():
    from pathlib import Path

    js = (Path(__file__).parents[1] / "src/sqlscout/static/app.js").read_text()
    assert "innerHTML" not in js and "eval(" not in js


def test_settings_defaults():
    assert Settings().max_rows == 200 and Settings().llm_provider == "none"
