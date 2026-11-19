import json

from sqlscout.cli import main


def test_init_db_and_sql(tmp_path, capsys, monkeypatch):
    db = tmp_path / "x.db"
    assert main(["init-db", "--db", str(db)]) == 0
    assert db.exists()
    assert main(["sql", "SELECT COUNT(*) AS n FROM orders", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "12000" in out and "n" in out


def test_sql_rejection_exit_code(db_path, capsys):
    assert main(["sql", "DROP TABLE orders", "--db", db_path]) == 1
    assert "Rejected" in capsys.readouterr().err


def test_missing_database_is_a_clean_error(tmp_path, capsys):
    assert main(["sql", "SELECT 1", "--db", str(tmp_path / "none.db")]) == 2
    assert "init-db" in capsys.readouterr().err


def test_eval_oracle_writes_report(db_path, tmp_path, capsys):
    out = tmp_path / "report.md"
    assert main(["eval", "--oracle", "--db", db_path, "--write", str(out)]) == 0
    text = out.read_text()
    assert "oracle" in text and "81/81" in text and "not a measurement" in text


def test_eval_without_model_reports_guard_only(db_path, tmp_path):
    out = tmp_path / "r.md"
    assert main(["eval", "--db", db_path, "--write", str(out)]) == 0
    text = out.read_text()
    assert "SQL guard" in text and "Agent accuracy" not in text


def test_ask_without_key_is_a_clean_error(db_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["ask", "How many orders?", "--db", db_path, "--provider", "anthropic"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_ask_json_output_with_scripted_agent(db_path, monkeypatch, capsys):
    from sqlscout import bootstrap
    from sqlscout.llm import LLMResponse, ScriptedLLM

    real = bootstrap.build_agent

    def fake(settings, llm=None, tools=None):
        return real(settings, llm=ScriptedLLM([LLMResponse(text="No data needed.")]))

    monkeypatch.setattr("sqlscout.bootstrap.build_agent", fake)
    assert main(["ask", "anything", "--db", db_path, "--provider", "ollama", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "no_query"
