from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import evaluate as ev
from .config import Settings
from .datagen import SEED, build
from .llm import LLMError

ORACLE_NOTE = (
    "Agent results below come from the oracle model, which answers every question with its gold "
    "SQL. It checks that the harness scores correctly. It is not a measurement of any real model."
)


def _settings(args) -> Settings:
    s = Settings()
    if getattr(args, "db", None):
        s = s.model_copy(update={"db_path": args.db})
    if getattr(args, "provider", None):
        s = s.model_copy(update={"llm_provider": args.provider})
    return s


def cmd_init_db(args) -> int:
    path = build(args.db or Settings().db_path, seed=args.seed)
    print(f"created {path}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app
    from .bootstrap import build_agent

    settings = _settings(args)
    agent = build_agent(settings, llm=_llm_or_null(settings))
    app = create_app(settings, agent, agent.tools)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _llm_or_null(settings: Settings):
    from .llm import NullLLM, make_llm

    try:
        return make_llm(settings)
    except LLMError as e:
        print(f"note: {e}; questions are disabled, the SQL console still works", file=sys.stderr)
        return NullLLM()


def cmd_ask(args) -> int:
    from .bootstrap import build_agent

    settings = _settings(args)
    try:
        agent = build_agent(settings)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    result = agent.run(args.question)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0
    print(result.answer)
    if result.sql:
        print(f"\nSQL: {result.sql}")
    print(
        f"[{result.status}, {len(result.steps)} steps, "
        f"{result.input_tokens + result.output_tokens} tokens]"
    )
    return 0 if result.status in ("ok", "no_query") else 1


def cmd_sql(args) -> int:
    from .bootstrap import build_toolbox

    out = build_toolbox(_settings(args)).call("run_sql", {"query": args.query})
    if out.result is None:
        print(out.content, file=sys.stderr)
        return 1
    print(out.sql)
    print("\t".join(out.result.columns))
    for row in out.result.rows:
        print("\t".join("" if v is None else str(v) for v in row))
    return 0


def cmd_eval(args) -> int:
    from .agent import Agent
    from .bootstrap import build_toolbox
    from .llm import make_llm

    settings = _settings(args)
    tools = build_toolbox(settings)
    questions = ev.load_jsonl(args.questions)
    if args.split:
        questions = [q for q in questions if q["split"] == args.split]

    summary, model, note = None, "", "Guard results are deterministic and need no model."
    if args.oracle or args.provider:
        llm = ev.OracleLLM(questions) if args.oracle else make_llm(settings)
        agent = Agent(llm, tools, settings.max_steps, settings.max_blocked, settings.token_budget)
        done = []

        def progress(o):
            done.append(o)
            mark = "pass" if o.passed else "FAIL"
            print(f"[{len(done)}/{len(questions)}] {o.id} {mark} ({o.reason})", file=sys.stderr)

        outcomes = ev.run_eval(agent, questions, tools, progress)
        summary, model = ev.summarise(outcomes), llm.name
        if args.oracle:
            note = ORACLE_NOTE
    guard = ev.evaluate_guard(tools, ev.load_jsonl(args.attacks))
    report = ev.render_markdown(model, summary, guard, note)
    print(report)
    if args.write:
        Path(args.write).write_text(report)
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import serve

    serve(_settings(args))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sqlscout", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    default_eval = ev.EVAL_DIR

    p = sub.add_parser("init-db", help="generate the demo database")
    p.add_argument("--db")
    p.add_argument("--seed", type=int, default=SEED)
    p.set_defaults(fn=cmd_init_db)

    p = sub.add_parser("serve", help="run the API and web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db")
    p.add_argument("--provider", choices=["none", "anthropic", "openai", "ollama"])
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("ask", help="ask one question from the terminal")
    p.add_argument("question")
    p.add_argument("--db")
    p.add_argument("--provider", choices=["anthropic", "openai", "ollama"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("sql", help="run one query through the guard")
    p.add_argument("query")
    p.add_argument("--db")
    p.set_defaults(fn=cmd_sql)

    p = sub.add_parser("eval", help="run the guard attack set and, optionally, the agent eval")
    p.add_argument("--oracle", action="store_true", help="harness self-check with gold SQL")
    p.add_argument("--provider", choices=["anthropic", "openai", "ollama"])
    p.add_argument("--split", choices=["dev", "test"])
    p.add_argument("--db")
    p.add_argument("--questions", default=str(default_eval / "questions.jsonl"))
    p.add_argument("--attacks", default=str(default_eval / "attacks.jsonl"))
    p.add_argument("--write")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("mcp", help="serve the tools over MCP (stdio)")
    p.add_argument("--db")
    p.set_defaults(fn=cmd_mcp)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
