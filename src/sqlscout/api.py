from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent import Agent
from .config import Settings
from .db import DatabaseError
from .guard import SQLRejected, validate
from .llm import LLMUnavailable, NullLLM
from .tools import ToolBox

STATIC = Path(__file__).parent / "static"

EXAMPLE_QUESTIONS = [
    "Which city has the most delivered revenue?",
    "What was the revenue in each month of 2025?",
    "Which product category has the highest gross margin on delivered orders?",
    "What percentage of orders were paid by UPI?",
]
EXAMPLE_QUERIES = [
    {
        "label": "Revenue by month",
        "sql": "SELECT strftime('%Y-%m', order_date) AS month, ROUND(SUM(total_inr)) AS revenue\n"
        "FROM orders WHERE status = 'delivered'\nGROUP BY month ORDER BY month",
    },
    {
        "label": "Orders by payment method",
        "sql": "SELECT payment_method, COUNT(*) AS orders\nFROM orders\n"
        "GROUP BY payment_method ORDER BY orders DESC",
    },
    {
        "label": "Top categories by revenue",
        "sql": "SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price_inr)) AS revenue\n"
        "FROM order_items oi\nJOIN orders o ON o.order_id = oi.order_id\n"
        "JOIN products p ON p.product_id = oi.product_id\n"
        "WHERE o.status = 'delivered'\nGROUP BY p.category ORDER BY revenue DESC",
    },
]
EXAMPLE_ATTACKS = [
    {"label": "Drop a table", "sql": "DROP TABLE orders"},
    {"label": "Read hidden column", "sql": "SELECT email FROM customers"},
    {"label": "Read system tables", "sql": "SELECT name FROM sqlite_master"},
    {"label": "Stacked statements", "sql": "SELECT 1; DELETE FROM orders"},
]


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class SqlIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                raise HTTPException(429, "Too many requests. Try again in a minute.")
            hits.append(now)


def create_app(settings: Settings, agent: Agent, tools: ToolBox) -> FastAPI:
    app = FastAPI(title="sqlscout", version=__version__, docs_url="/api/docs", redoc_url=None)
    limiter = RateLimiter(settings.rate_limit_per_min)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/docs") or request.url.path == "/openapi.json":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net data:"
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = (
            "no-store"
            if request.url.path.startswith("/api")
            else response.headers.get("Cache-Control", "no-cache")
        )
        return response

    def guard_request(request: Request) -> None:
        if settings.api_key:
            given = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(given.encode(), settings.api_key.encode()):
                raise HTTPException(401, "Missing or wrong API key.")
        limiter.check(request.client.host if request.client else "unknown")

    @app.get("/api/health")
    def health():
        llm_ready = not isinstance(agent.llm, NullLLM)
        return {
            "status": "ok",
            "version": __version__,
            "model": agent.llm.name if llm_ready else None,
            "tables": [t.name for t in tools.catalog.tables],
            "auth_required": bool(settings.api_key),
        }

    @app.get("/api/schema")
    def schema():
        return {
            "tables": [
                {
                    "name": t.name,
                    "description": t.description,
                    "rows": t.rows,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.type,
                            "description": c.description,
                            "samples": list(c.samples),
                        }
                        for c in t.columns
                    ],
                }
                for t in tools.catalog.tables
            ],
            "restricted": {t: sorted(c) for t, c in tools.catalog.restricted.items()},
        }

    @app.get("/api/examples")
    def examples():
        return {
            "questions": EXAMPLE_QUESTIONS,
            "queries": EXAMPLE_QUERIES,
            "attacks": EXAMPLE_ATTACKS,
        }

    @app.post("/api/sql", dependencies=[Depends(guard_request)])
    def run_sql(body: SqlIn):
        try:
            checked = validate(body.query, tools.policy)
        except SQLRejected as e:
            return JSONResponse({"error": {"kind": e.kind, "message": e.message}}, status_code=422)
        try:
            result = tools.db.execute(checked.sql)
        except DatabaseError as e:
            return JSONResponse({"error": {"kind": "runtime", "message": str(e)}}, status_code=422)
        return {
            "sql": checked.sql,
            "notes": list(checked.notes),
            "columns": list(result.columns),
            "rows": [list(r) for r in result.rows],
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
        }

    @app.post("/api/ask", dependencies=[Depends(guard_request)])
    def ask(body: AskIn):
        if isinstance(agent.llm, NullLLM):
            raise HTTPException(
                503,
                "No model is configured, so questions are disabled. The SQL console still works.",
            )
        try:
            result = agent.run(body.question)
        except LLMUnavailable as e:
            raise HTTPException(503, str(e)) from None
        return result.to_dict()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
