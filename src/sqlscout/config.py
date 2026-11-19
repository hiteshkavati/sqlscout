from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Environment variables use the SQ_ prefix."""

    model_config = SettingsConfigDict(env_prefix="SQ_", env_file=".env", extra="ignore")

    db_path: str = "data/shop.db"

    # guard
    max_rows: int = 200
    default_limit: int = 100
    query_timeout_s: float = 5.0

    # agent
    max_steps: int = 8
    max_blocked: int = 3
    token_budget: int = 40_000

    # model
    llm_provider: Literal["none", "anthropic", "openai", "ollama"] = "none"
    anthropic_model: str = "claude-haiku-5-5"
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_model: str = "llama3.1:8b"
    ollama_url: str = "http://localhost:11434/v1"
    llm_timeout_s: float = 60.0

    # service
    api_key: str | None = None
    rate_limit_per_min: int = 30
    trace_dir: str | None = None
