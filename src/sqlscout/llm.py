"""Model clients with tool calling, over plain httpx.

Conversations use one neutral message format that each client translates:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from .config import Settings


class LLMError(Exception):
    """The model call failed in a way retrying will not fix."""


class LLMUnavailable(LLMError):
    """No model is configured."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


class LLM:
    name = "base"

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        raise NotImplementedError


def _post_with_retry(
    client: httpx.Client, url: str, headers: dict, payload: dict, attempts: int = 3
) -> dict:
    delay = 1.0
    for attempt in range(attempts):
        try:
            resp = client.post(url, headers=headers, json=payload)
        except httpx.TransportError as e:
            if attempt == attempts - 1:
                raise LLMError(f"network error: {e.__class__.__name__}") from None
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code in (408, 429) or resp.status_code >= 500:
            if attempt == attempts - 1:
                raise LLMError(f"model API returned {resp.status_code}")
            wait = resp.headers.get("retry-after")
            time.sleep(min(float(wait), 20) if wait and wait.replace(".", "").isdigit() else delay)
            delay *= 2
            continue
        if resp.status_code >= 400:
            raise LLMError(f"model API returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    raise LLMError("unreachable")


class AnthropicLLM(LLM):
    def __init__(
        self, api_key: str, model: str, timeout: float = 60.0, client: httpx.Client | None = None
    ):
        self.name = f"anthropic:{model}"
        self.model = model
        self._key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls", []):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": c["id"],
                            "name": c["name"],
                            "input": c["arguments"],
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
            else:  # tool result; consecutive results share one user turn
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                    "is_error": bool(m.get("is_error")),
                }
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def chat(self, system, messages, tools):
        payload = {
            "model": self.model,
            "max_tokens": 1500,
            "temperature": 0,
            "system": system,
            "messages": self._convert(messages),
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ],
        }
        headers = {
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = _post_with_retry(
            self._client, "https://api.anthropic.com/v1/messages", headers, payload
        )
        text, calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text.append(block["text"])
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(block["id"], block["name"], block.get("input") or {}))
        usage = data.get("usage", {})
        return LLMResponse(
            "".join(text).strip(),
            calls,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            data.get("stop_reason", ""),
        )


class OpenAICompatLLM(LLM):
    """OpenAI, Ollama (/v1) and other chat-completions servers."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        label: str = "openai",
        client: httpx.Client | None = None,
    ):
        self.name = f"{label}:{model}"
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    @staticmethod
    def _convert(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c["arguments"]),
                            },
                        }
                        for c in m["tool_calls"]
                    ]
                out.append(msg)
            else:
                out.append(
                    {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
                )
        return out

    def chat(self, system, messages, tools):
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": self._convert(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ],
        }
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        data = _post_with_retry(self._client, self._url, headers, payload)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError):
            raise LLMError("unexpected response shape from model API") from None
        calls = []
        for c in message.get("tool_calls") or []:
            raw = c["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"_invalid_json": raw[:200]}
            calls.append(ToolCall(c.get("id") or f"call_{len(calls)}", c["function"]["name"], args))
        usage = data.get("usage", {})
        return LLMResponse(
            (message.get("content") or "").strip(),
            calls,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            choice.get("finish_reason", ""),
        )


class NullLLM(LLM):
    name = "none"

    def chat(self, system, messages, tools):
        raise LLMUnavailable("No model configured. Set SQ_LLM_PROVIDER and its API key.")


class ScriptedLLM(LLM):
    """Deterministic stand-in used by tests and the harness self-check."""

    name = "scripted"

    def __init__(self, script: list[LLMResponse] | Callable[[list[dict], list[dict]], LLMResponse]):
        self._script = script
        self.calls: list[list[dict]] = []

    def chat(self, system, messages, tools):
        self.calls.append([dict(m) for m in messages])
        if callable(self._script):
            return self._script(messages, tools)
        index = sum(1 for m in messages if m["role"] == "assistant")
        if index >= len(self._script):
            raise LLMError("script exhausted")
        return self._script[index]


def make_llm(settings: Settings) -> LLM:
    import os

    provider = settings.llm_provider
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        return AnthropicLLM(key, settings.anthropic_model, settings.llm_timeout_s)
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        return OpenAICompatLLM(
            settings.openai_base_url, settings.openai_model, key, settings.llm_timeout_s
        )
    if provider == "ollama":
        return OpenAICompatLLM(
            settings.ollama_url, settings.ollama_model, None, settings.llm_timeout_s, label="ollama"
        )
    return NullLLM()
