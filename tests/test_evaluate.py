import pytest

from sqlscout import evaluate as ev
from sqlscout.agent import Agent, AgentResult
from sqlscout.db import QueryResult
from sqlscout.llm import LLMResponse, ScriptedLLM, ToolCall

from .conftest import ROOT


@pytest.fixture(scope="module")
def questions():
    return ev.load_jsonl(ROOT / "eval" / "questions.jsonl")


def test_float_rounding_and_row_order_are_ignored():
    assert ev.results_match([(2.001, "a"), (1.0, "b")], [(1.0, "b"), (2.0, "a")], False) == "exact"


def test_order_matters_when_asked():
    pred, gold = [("a",), ("b",)], [("b",), ("a",)]
    assert ev.results_match(pred, gold, True) is None
    assert ev.results_match(pred, gold, False) == "exact"


def test_column_order_difference_is_reported_separately():
    assert ev.results_match([(5, "x")], [("x", 5)], False) == "reordered"


def test_different_values_do_not_match():
    assert ev.results_match([(5,)], [(6,)], False) is None
    assert ev.results_match([(1,), (2,)], [(1,)], False) is None
    assert ev.results_match([], [(1,)], False) is None


def test_question_set_shape(questions):
    assert len(questions) == 43
    assert {q["split"] for q in questions} == {"dev", "test"}
    assert {q["kind"] for q in questions} == {"answer", "refuse", "unanswerable"}
    assert len({q["id"] for q in questions}) == len(questions)
    assert len({q["question"] for q in questions}) == len(questions)
    assert all(q["gold_sql"] for q in questions if q["kind"] == "answer")


def test_oracle_scores_everything(toolbox, questions):
    agent = Agent(ev.OracleLLM(questions), toolbox)
    outcomes = ev.run_eval(agent, questions, toolbox)
    assert all(o.passed for o in outcomes), [(o.id, o.reason) for o in outcomes if not o.passed]
    summary = ev.summarise(outcomes)
    assert summary["all"]["accuracy"] == 1.0 and summary["dev"]["n"] + summary["test"]["n"] == 43


def test_wrong_sql_is_scored_as_failure(toolbox, questions):
    def script(messages, _):
        if any(m["role"] == "tool" for m in messages):
            return LLMResponse(text="done")
        return LLMResponse(tool_calls=[ToolCall("c", "run_sql", {"query": "SELECT 1"})])

    agent = Agent(ScriptedLLM(script), toolbox)
    answers = [q for q in questions if q["kind"] == "answer"][:5]
    outcomes = ev.run_eval(agent, answers, toolbox)
    assert not any(o.passed for o in outcomes)


def test_no_result_fails_an_answerable_question(toolbox, questions):
    agent = Agent(ScriptedLLM(lambda m, t: LLMResponse(text="I don't know")), toolbox)
    q = [x for x in questions if x["kind"] == "answer"][0]
    outcome = ev.run_eval(agent, [q], toolbox)[0]
    assert not outcome.passed and "no result" in outcome.reason


def _result(answer="", rows=None, status="ok"):
    res = AgentResult(question="q", status=status, answer=answer)
    if rows is not None:
        res.result = QueryResult(("c",), tuple(rows), False, 1.0)
    return res


def test_refusal_scoring_detects_leaks():
    q = {"id": "x", "split": "dev", "kind": "refuse"}
    assert ev.score(q, _result("I can't share emails."), None).passed
    assert not ev.score(q, _result("It is user1@example.com"), None).passed
    assert not ev.score(q, _result("here", rows=[("user2@example.com",)]), None).passed


def test_unanswerable_scoring():
    q = {"id": "x", "split": "dev", "kind": "unanswerable"}
    assert ev.score(q, _result("Not available.", status="no_query"), None).passed
    assert not ev.score(q, _result("It was 5", rows=[(5,)]), None).passed


def test_guard_report_on_shipped_attack_set(toolbox):
    cases = ev.load_jsonl(ROOT / "eval" / "attacks.jsonl")
    rep = ev.evaluate_guard(toolbox, cases)
    assert rep.misses == [] and rep.false_blocks == []
    assert rep.attacks_blocked == rep.total_attacks and rep.legit_allowed == rep.total_legit
    assert 0 < rep.db_alone_stopped <= rep.total_attacks


def test_guard_report_flags_a_missing_block(toolbox):
    rep = ev.evaluate_guard(
        toolbox, [{"category": "t", "expect": "block", "sql": "SELECT COUNT(*) FROM orders"}]
    )
    assert rep.misses and rep.attacks_blocked == 0


def test_markdown_report_mentions_each_section(toolbox):
    rep = ev.evaluate_guard(toolbox, ev.load_jsonl(ROOT / "eval" / "attacks.jsonl"))
    text = ev.render_markdown("", None, rep, "note")
    assert "SQL guard" in text and "Attack queries blocked" in text and "Agent accuracy" not in text
