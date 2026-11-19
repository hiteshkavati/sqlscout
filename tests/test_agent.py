import json

from sqlscout.agent import BLOCKED, BUDGET, MAX_STEPS, MODEL_ERROR, NO_QUERY, OK
from sqlscout.llm import LLMError, LLMResponse, ScriptedLLM, ToolCall


def sql_call(query, cid="c"):
    return LLMResponse(
        tool_calls=[ToolCall(cid, "run_sql", {"query": query})], input_tokens=50, output_tokens=10
    )


def final(text="Answer.", tokens=(20, 5)):
    return LLMResponse(text=text, input_tokens=tokens[0], output_tokens=tokens[1])


def test_happy_path(make_agent):
    llm = ScriptedLLM([sql_call("SELECT COUNT(*) AS n FROM orders"), final("There are 12000.")])
    res = make_agent(llm).run("How many orders?")
    assert res.status == OK and res.answer == "There are 12000."
    assert res.result.rows == ((12000,),) and "COUNT" in res.sql
    assert [s.tool for s in res.steps] == ["run_sql"]
    assert res.input_tokens == 70 and res.output_tokens == 15


def test_model_sees_tool_result_and_system_prompt_has_schema(make_agent):
    llm = ScriptedLLM([sql_call("SELECT COUNT(*) FROM orders"), final()])
    agent = make_agent(llm)
    agent.run("q")
    second_call = llm.calls[1]
    tool_msg = [m for m in second_call if m["role"] == "tool"][0]
    assert json.loads(tool_msg["content"])["rows"] == [[12000]]
    assert "orders" in agent.system and "total_inr" in agent.system
    assert "email" not in agent.system  # restricted column is never shown to the model


def test_recovers_after_invalid_query(make_agent):
    llm = ScriptedLLM([sql_call("SELECT nope FROM orders"), sql_call("SELECT 1 AS x"), final()])
    res = make_agent(llm).run("q")
    assert res.status == OK and [s.is_error for s in res.steps] == [True, False]
    assert res.steps[0].blocked == "invalid"


def test_repeated_blocked_queries_stop_the_run(make_agent):
    llm = ScriptedLLM(
        [
            sql_call("DROP TABLE orders"),
            sql_call("DELETE FROM orders"),
            sql_call("SELECT email FROM customers"),
            final(),
        ]
    )
    res = make_agent(llm, max_blocked=3).run("q")
    assert res.status == BLOCKED and res.result is None and len(res.steps) == 3


def test_step_limit(make_agent):
    llm = ScriptedLLM(lambda m, t: sql_call("SELECT 1"))
    res = make_agent(llm, max_steps=3).run("q")
    assert res.status == MAX_STEPS and len(res.steps) == 3


def test_token_budget(make_agent):
    llm = ScriptedLLM(
        lambda m, t: LLMResponse(
            tool_calls=[ToolCall("c", "run_sql", {"query": "SELECT 1"})],
            input_tokens=900,
            output_tokens=200,
        )
    )
    res = make_agent(llm, token_budget=1000, max_steps=10).run("q")
    assert res.status == BUDGET and len(res.steps) == 1


def test_answer_without_query_is_flagged(make_agent):
    res = make_agent(ScriptedLLM([final("That is not in this database.")])).run("rating?")
    assert res.status == NO_QUERY and res.result is None


def test_model_failure_is_reported_not_raised(make_agent):
    def boom(m, t):
        raise LLMError("API returned 500")

    res = make_agent(ScriptedLLM(boom)).run("q")
    assert res.status == MODEL_ERROR and "500" in res.error


def test_last_successful_query_is_kept(make_agent):
    llm = ScriptedLLM([sql_call("SELECT 1 AS a"), sql_call("DROP TABLE orders"), final()])
    res = make_agent(llm).run("q")
    assert res.result.columns == ("a",)


def test_unknown_tool_is_handled(make_agent):
    llm = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("c", "hack", {})]), final()])
    res = make_agent(llm).run("q")
    assert res.steps[0].is_error and res.status == NO_QUERY


def test_trace_file_is_written_without_rows(make_agent, tmp_path):
    llm = ScriptedLLM([sql_call("SELECT COUNT(*) FROM orders"), final()])
    make_agent(llm, trace_dir=str(tmp_path)).run("How many?")
    line = next(tmp_path.glob("*.jsonl")).read_text().strip()
    record = json.loads(line)
    assert record["question"] == "How many?" and "rows" not in record
