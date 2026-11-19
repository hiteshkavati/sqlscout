from __future__ import annotations

from .agent import Agent
from .config import Settings
from .db import Database, load_catalog
from .llm import LLM, make_llm
from .tools import ToolBox


def build_toolbox(settings: Settings) -> ToolBox:
    catalog = load_catalog(settings.db_path)
    policy = catalog.policy(settings.default_limit, settings.max_rows)
    db = Database(settings.db_path, policy, settings.max_rows, settings.query_timeout_s)
    return ToolBox(db, catalog)


def build_agent(settings: Settings, llm: LLM | None = None, tools: ToolBox | None = None) -> Agent:
    return Agent(
        llm or make_llm(settings),
        tools or build_toolbox(settings),
        max_steps=settings.max_steps,
        max_blocked=settings.max_blocked,
        token_budget=settings.token_budget,
        trace_dir=settings.trace_dir,
    )
