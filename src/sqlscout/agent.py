"""The agent loop: model <-> tools, with step, block and token budgets."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .db import QueryResult
from .llm import LLM, LLMError
from .prompts import build_system
from .tools import ToolBox

OK = "ok"
NO_QUERY = "no_query"  # answered without running a successful query
MAX_STEPS = "max_steps"
BLOCKED = "blocked_limit"
BUDGET = "token_budget"
MODEL_ERROR = "model_error"


@dataclass
class Step:
    index: int
    tool: str
    arguments: dict
    output: str  # truncated copy of what the model saw
    is_error: bool
    blocked: str | None
    sql: str | None
    ms: float


@dataclass
class AgentResult:
    question: str
    status: str
    answer: str
    sql: str | None = None
    result: QueryResult | None = None
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    error: str | None = None
    elapsed_ms: float = 0.0

    def to_dict(self, include_rows: bool = True) -> dict:
        d = {
            "question": self.question,
            "status": self.status,
            "answer": self.answer,
            "sql": self.sql,
            "model": self.model,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "steps": [asdict(s) for s in self.steps],
        }
        if include_rows and self.result is not None:
            d["columns"] = list(self.result.columns)
            d["rows"] = [list(r) for r in self.result.rows]
            d["truncated"] = self.result.truncated
        return d


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: ToolBox,
        max_steps: int = 8,
        max_blocked: int = 3,
        token_budget: int = 40_000,
        trace_dir: str | None = None,
    ):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.max_blocked = max_blocked
        self.token_budget = token_budget
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.system = build_system(tools.catalog.describe())

    def run(self, question: str) -> AgentResult:
        started = time.monotonic()
        res = AgentResult(question=question, status=MAX_STEPS, answer="", model=self.llm.name)
        messages: list[dict] = [{"role": "user", "content": question}]
        blocked = 0
        try:
            for _ in range(self.max_steps):
                resp = self.llm.chat(self.system, messages, self.tools.specs)
                res.input_tokens += resp.input_tokens
                res.output_tokens += resp.output_tokens
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.text,
                        "tool_calls": [
                            {"id": c.id, "name": c.name, "arguments": c.arguments}
                            for c in resp.tool_calls
                        ],
                    }
                )
                if not resp.tool_calls:
                    res.answer = resp.text
                    res.status = OK if res.result is not None else NO_QUERY
                    break
                for call in resp.tool_calls:
                    t0 = time.monotonic()
                    out = self.tools.call(call.name, call.arguments)
                    res.steps.append(
                        Step(
                            len(res.steps) + 1,
                            call.name,
                            call.arguments,
                            out.content[:600],
                            out.is_error,
                            out.blocked,
                            out.sql,
                            round((time.monotonic() - t0) * 1000, 2),
                        )
                    )
                    if out.blocked:
                        blocked += 1
                    if out.result is not None:
                        res.result, res.sql = out.result, out.sql
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": out.content,
                            "is_error": out.is_error,
                        }
                    )
                if blocked >= self.max_blocked:
                    res.status = BLOCKED
                    res.answer = "I could not answer this within the query restrictions."
                    break
                if res.input_tokens + res.output_tokens > self.token_budget:
                    res.status = BUDGET
                    res.answer = "Stopped: the token budget for this question was used up."
                    break
            else:
                res.answer = "I could not finish within the step limit."
        except LLMError as e:
            res.status, res.error, res.answer = MODEL_ERROR, str(e), "The model call failed."
        res.elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        self._trace(res)
        return res

    def _trace(self, res: AgentResult) -> None:
        if not self.trace_dir:
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / f"{time.strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(res.to_dict(include_rows=False), default=str) + "\n")
