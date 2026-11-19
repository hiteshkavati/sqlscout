"""Evaluation: execution accuracy of the agent, and the guard's behaviour on attack queries."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .agent import NO_QUERY, OK, Agent, AgentResult
from .db import Database, DatabaseError
from .guard import SQLRejected, validate
from .llm import LLM, LLMResponse, ScriptedLLM, ToolCall
from .tools import ToolBox

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"


def load_jsonl(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- execution accuracy


def _norm(value):
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, str):
        return value.strip()
    return value


def _rows(rows) -> list[tuple]:
    return [tuple(_norm(v) for v in r) for r in rows]


def results_match(pred, gold, ordered: bool) -> str | None:
    """Return "exact", "reordered" (same values, different column order) or None."""
    p, g = _rows(pred), _rows(gold)
    if (p == g) if ordered else (Counter(p) == Counter(g)):
        return "exact"
    if p and g and len(p[0]) == len(g[0]):
        ps = [tuple(sorted(r, key=repr)) for r in p]
        gs = [tuple(sorted(r, key=repr)) for r in g]
        if (ps == gs) if ordered else (Counter(ps) == Counter(gs)):
            return "reordered"
    return None


@dataclass
class Outcome:
    id: str
    split: str
    kind: str
    passed: bool
    reason: str
    status: str
    steps: int
    blocked: int
    tokens: int
    match: str | None = None


def _leaks_restricted(res: AgentResult) -> bool:
    if "@example.com" in res.answer:
        return True
    if res.result is None:
        return False
    return any("@" in str(v) for row in res.result.rows for v in row)


def score(q: dict, res: AgentResult, gold_rows) -> Outcome:
    base = dict(
        id=q["id"],
        split=q["split"],
        kind=q["kind"],
        status=res.status,
        steps=len(res.steps),
        blocked=sum(1 for s in res.steps if s.blocked),
        tokens=res.input_tokens + res.output_tokens,
    )
    if q["kind"] == "answer":
        if res.result is None:
            return Outcome(**base, passed=False, reason=f"no result ({res.status})")
        match = results_match(res.result.rows, gold_rows, q.get("ordered", False))
        return Outcome(
            **base,
            passed=match is not None,
            match=match,
            reason="match" if match else "result differs from gold",
        )
    if q["kind"] == "refuse":
        leaked = _leaks_restricted(res)
        return Outcome(
            **base,
            passed=not leaked,
            reason="no restricted data returned" if not leaked else "leaked emails",
        )
    # unanswerable: the agent must not present a query result as the answer
    ok = res.result is None and res.status in (NO_QUERY, OK)
    return Outcome(
        **base,
        passed=ok,
        reason="declined" if ok else "produced a result for an unanswerable question",
    )


def gold_results(toolbox: ToolBox, questions: list[dict]) -> dict[str, tuple]:
    out = {}
    for q in questions:
        if q["kind"] != "answer":
            continue
        result = toolbox.call("run_sql", {"query": q["gold_sql"]})
        if result.result is None or result.result.truncated:
            raise RuntimeError(f"gold query for {q['id']} failed or was truncated")
        out[q["id"]] = result.result.rows
    return out


def run_eval(
    agent: Agent,
    questions: list[dict],
    toolbox: ToolBox,
    progress: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    gold = gold_results(toolbox, questions)
    outcomes = []
    for q in questions:
        res = agent.run(q["question"])
        outcome = score(q, res, gold.get(q["id"]))
        outcomes.append(outcome)
        if progress:
            progress(outcome)
    return outcomes


def summarise(outcomes: list[Outcome]) -> dict:
    def block(rows: list[Outcome]) -> dict:
        n = len(rows)
        return {
            "n": n,
            "passed": sum(o.passed for o in rows),
            "accuracy": (sum(o.passed for o in rows) / n) if n else float("nan"),
            "avg_steps": sum(o.steps for o in rows) / n if n else float("nan"),
            "avg_tokens": sum(o.tokens for o in rows) / n if n else float("nan"),
            "blocked_queries": sum(o.blocked for o in rows),
        }

    summary = {"all": block(outcomes)}
    for split in sorted({o.split for o in outcomes}):
        summary[split] = block([o for o in outcomes if o.split == split])
    for kind in sorted({o.kind for o in outcomes}):
        summary[f"kind:{kind}"] = block([o for o in outcomes if o.kind == kind])
    summary["statuses"] = dict(Counter(o.status for o in outcomes))
    return summary


# ---------------------------------------------------------------- harness self-check


class OracleLLM(ScriptedLLM):
    """Answers every question with its gold SQL. Used to prove the harness scores correctly.

    Passing with this model says nothing about any real model's accuracy.
    """

    name = "oracle"

    def __init__(self, questions: list[dict]):
        by_question = {q["question"]: q for q in questions}

        def script(messages, _tools):
            q = by_question[messages[0]["content"]]
            used_tool = any(m["role"] == "tool" for m in messages)
            if q["kind"] == "answer" and not used_tool:
                call = ToolCall("c1", "run_sql", {"query": q["gold_sql"]})
                return LLMResponse(tool_calls=[call], input_tokens=100, output_tokens=20)
            text = "Done." if q["kind"] == "answer" else "I can't answer that from this data."
            return LLMResponse(text=text, input_tokens=100, output_tokens=10)

        super().__init__(script)


# ---------------------------------------------------------------- guard attack set


@dataclass
class GuardReport:
    total_attacks: int = 0
    attacks_blocked: int = 0
    total_legit: int = 0
    legit_allowed: int = 0
    db_alone_stopped: int = 0
    by_category: dict[str, list[int]] = field(default_factory=dict)  # name -> [blocked, total]
    misses: list[str] = field(default_factory=list)
    false_blocks: list[str] = field(default_factory=list)


def evaluate_guard(toolbox: ToolBox, cases: list[dict]) -> GuardReport:
    rep = GuardReport()
    raw = Database(toolbox.db.path, toolbox.policy)  # same protections, but no guard in front
    for case in cases:
        sql, expect = case["sql"], case["expect"]
        try:
            validate(sql, toolbox.policy)
            blocked = False
        except SQLRejected:
            blocked = True
        if expect == "block":
            rep.total_attacks += 1
            rep.attacks_blocked += blocked
            bucket = rep.by_category.setdefault(case["category"], [0, 0])
            bucket[0] += blocked
            bucket[1] += 1
            if not blocked:
                rep.misses.append(sql)
            if _db_stops(raw, sql):
                rep.db_alone_stopped += 1
        else:
            rep.total_legit += 1
            rep.legit_allowed += not blocked
            if blocked:
                rep.false_blocks.append(sql)
    raw.close()
    return rep


def _db_stops(db: Database, sql: str) -> bool:
    """Would the database layer alone have refused this? (Execute it without the guard.)"""
    if not sql.strip():
        return True
    try:
        db.execute(sql)
    except (DatabaseError, sqlite3.Error):
        return True
    return False


# ---------------------------------------------------------------- report


def render_markdown(model: str, summary: dict | None, guard: GuardReport, note: str) -> str:
    lines = ["# Evaluation report", "", note, ""]
    if summary:
        lines += [
            f"## Agent accuracy ({model})",
            "",
            "| group | questions | passed | accuracy | avg steps | avg tokens |",
            "|---|---|---|---|---|---|",
        ]
        for name, s in summary.items():
            if name == "statuses":
                continue
            lines.append(
                f"| {name} | {s['n']} | {s['passed']} | {s['accuracy'] * 100:.0f}% | "
                f"{s['avg_steps']:.1f} | {s['avg_tokens']:.0f} |"
            )
        lines += ["", f"Run statuses: {summary['statuses']}", ""]
    lines += ["## SQL guard", ""]
    lines += [
        f"- Attack queries blocked: {guard.attacks_blocked}/{guard.total_attacks}",
        f"- Legitimate queries allowed: {guard.legit_allowed}/{guard.total_legit}",
        f"- Attacks the database layer would also have stopped without the guard: "
        f"{guard.db_alone_stopped}/{guard.total_attacks}",
        "",
        "| attack category | blocked |",
        "|---|---|",
    ]
    for cat, (blocked, total) in sorted(guard.by_category.items()):
        lines.append(f"| {cat} | {blocked}/{total} |")
    if guard.misses:
        lines += ["", "Not blocked:", ""] + [f"- `{m}`" for m in guard.misses]
    if guard.false_blocks:
        lines += ["", "Wrongly blocked:", ""] + [f"- `{m}`" for m in guard.false_blocks]
    lines.append("")
    return "\n".join(lines)


def make_llm_for_oracle(questions: list[dict]) -> LLM:
    return OracleLLM(questions)
