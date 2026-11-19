import json

import httpx
import pytest

from sqlscout.config import Settings
from sqlscout.llm import (
    AnthropicLLM,
    LLMError,
    LLMResponse,
    LLMUnavailable,
    NullLLM,
    OpenAICompatLLM,
    ScriptedLLM,
    make_llm,
)

TOOLS = [
    {
        "name": "run_sql",
        "description": "d",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
]
HISTORY = [
    {"role": "user", "content": "how many orders?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "a", "name": "run_sql", "arguments": {"query": "SELECT 1"}},
            {"id": "b", "name": "run_sql", "arguments": {"query": "SELECT 2"}},
        ],
    },
    {"role": "tool", "tool_call_id": "a", "name": "run_sql", "content": "r1", "is_error": False},
    {"role": "tool", "tool_call_id": "b", "name": "run_sql", "content": "bad", "is_error": True},
]


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_request_shape_and_parsing():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "ok"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "run_sql",
                        "input": {"query": "SELECT 1"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
                "stop_reason": "tool_use",
            },
        )

    llm = AnthropicLLM("key", "m", client=client(handler))
    r = llm.chat("sys", HISTORY, TOOLS)
    body = seen["body"]
    assert seen["headers"]["x-api-key"] == "key"
    assert body["system"] == "sys" and body["tools"][0]["input_schema"]["type"] == "object"
    # both tool results are grouped into a single user turn after the assistant turn
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    results = body["messages"][2]["content"]
    assert [b["tool_use_id"] for b in results] == ["a", "b"] and results[1]["is_error"] is True
    assert r.text == "ok" and r.tool_calls[0].arguments == {"query": "SELECT 1"}
    assert (r.input_tokens, r.output_tokens) == (10, 4)


def test_openai_request_shape_and_parsing():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_sql",
                                        "arguments": '{"query": "SELECT 1"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    llm = OpenAICompatLLM("https://x.test/v1/", "m", "sk", client=client(handler))
    r = llm.chat("sys", HISTORY, TOOLS)
    msgs = seen["body"]["messages"]
    assert seen["auth"] == "Bearer sk" and msgs[0] == {"role": "system", "content": "sys"}
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool"]
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"query": "SELECT 1"}
    assert r.tool_calls[0].arguments == {"query": "SELECT 1"} and r.input_tokens == 7


def test_openai_invalid_tool_arguments_do_not_crash():
    def handler(_):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "x",
                                    "function": {"name": "run_sql", "arguments": "{not json"},
                                }
                            ]
                        }
                    }
                ]
            },
        )

    r = OpenAICompatLLM("https://x.test/v1", "m", client=client(handler)).chat("s", HISTORY, TOOLS)
    assert "_invalid_json" in r.tool_calls[0].arguments


def test_ollama_needs_no_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    llm = OpenAICompatLLM(
        "http://localhost:11434/v1", "llama", None, label="ollama", client=client(handler)
    )
    assert llm.chat("s", HISTORY[:1], TOOLS).text == "hi" and seen["auth"] is None
    assert llm.name == "ollama:llama"


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = []

    def handler(_):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    assert AnthropicLLM("k", "m", client=client(handler)).chat("s", HISTORY[:1], TOOLS).text == "ok"
    assert len(calls) == 3


def test_gives_up_after_repeated_500(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(LLMError, match="500"):
        AnthropicLLM("k", "m", client=client(lambda _: httpx.Response(500))).chat(
            "s", HISTORY[:1], TOOLS
        )


def test_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = []

    def handler(_):
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with pytest.raises(LLMError, match="401"):
        AnthropicLLM("k", "m", client=client(handler)).chat("s", HISTORY[:1], TOOLS)
    assert len(calls) == 1


def test_network_failure_becomes_llm_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(_):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError, match="network"):
        AnthropicLLM("k", "m", client=client(handler)).chat("s", HISTORY[:1], TOOLS)


def test_make_llm_selection(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(make_llm(Settings(llm_provider="none")), NullLLM)
    with pytest.raises(LLMUnavailable):
        make_llm(Settings(llm_provider="anthropic"))
    with pytest.raises(LLMUnavailable):
        make_llm(Settings(llm_provider="openai"))
    assert make_llm(Settings(llm_provider="ollama")).name.startswith("ollama:")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert make_llm(Settings(llm_provider="anthropic")).name.startswith("anthropic:")


def test_null_llm_refuses():
    with pytest.raises(LLMUnavailable):
        NullLLM().chat("s", [], [])


def test_scripted_llm_follows_turns_and_runs_out():
    llm = ScriptedLLM([LLMResponse(text="one"), LLMResponse(text="two")])
    assert llm.chat("s", [{"role": "user", "content": "q"}], []).text == "one"
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "one"}]
    assert llm.chat("s", msgs, []).text == "two"
    with pytest.raises(LLMError):
        llm.chat("s", msgs + [{"role": "assistant", "content": "two"}], [])
