"""Read-only execution with database-level protections.

Even if the guard had a gap, the connection itself is opened read-only, runs with
`query_only`, and installs an authorizer that only permits reading allowed columns and
calling allowed functions. A progress handler enforces the time limit.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .guard import ALLOWED_ANONYMOUS, Policy
from .schema import Catalog, introspect

# SQLite built-ins that sqlglot models as typed functions and that are safe to call.
_SQLITE_PURE_FUNCTIONS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "total",
        "abs",
        "round",
        "coalesce",
        "ifnull",
        "nullif",
        "iif",
        "length",
        "lower",
        "upper",
        "substr",
        "replace",
        "trim",
        "ltrim",
        "rtrim",
        "instr",
        "cast",
        "strftime",
        "date",
        "time",
        "datetime",
        "julianday",
        "unixepoch",
        "group_concat",
        "printf",
        "typeof",
        "like",
        "glob",
        "hex",
        "quote",
        "sign",
        "row_number",
        "rank",
        "dense_rank",
        "ntile",
        "lag",
        "lead",
        "first_value",
        "last_value",
        "nth_value",
        "percent_rank",
        "cume_dist",
        "ceil",
        "ceiling",
        "floor",
        "sqrt",
        "power",
        "pow",
        "ln",
        "log",
        "exp",
        "mod",
        "unicode",
        "char",
        "random",
    }
)
_DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "random"})


class DatabaseError(Exception):
    """A query failed at run time (timeout, SQL error, denied access)."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    truncated: bool
    elapsed_ms: float

    def as_dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]


class Database:
    def __init__(
        self, path: str | Path, policy: Policy, max_rows: int = 200, timeout_s: float = 5.0
    ):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} not found. Run `sqlscout init-db` first.")
        self.policy = policy
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self._allowed_tables = {t.lower() for t in policy.tables}
        self._hidden = {
            (t.lower(), c.lower()) for t, cols in policy.restricted.items() for c in cols
        }
        self._lock = threading.Lock()  # one connection, one query at a time
        self._conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(self._authorize)
        return conn

    def _authorize(self, action, arg1, arg2, _db, _source) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if table not in self._allowed_tables:
                return sqlite3.SQLITE_DENY
            if (table, column) in self._hidden:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            name = (arg2 or "").lower()
            if name in _DENIED_FUNCTIONS:
                return sqlite3.SQLITE_DENY
            if name in _SQLITE_PURE_FUNCTIONS or name in ALLOWED_ANONYMOUS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    def execute(self, sql: str) -> QueryResult:
        with self._lock:
            return self._execute_locked(sql)

    def _execute_locked(self, sql: str) -> QueryResult:
        deadline = time.monotonic() + self.timeout_s
        self._conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
        started = time.monotonic()
        try:
            cur = self._conn.execute(sql)
            columns = tuple(d[0] for d in cur.description or ())
            rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                raise DatabaseError(f"Query exceeded the {self.timeout_s:g}s time limit.") from None
            raise DatabaseError(f"SQL error: {e}") from None
        except sqlite3.DatabaseError as e:
            if "not authorized" in str(e).lower():
                raise DatabaseError("Access denied by the database.") from None
            raise DatabaseError(f"SQL error: {e}") from None
        finally:
            self._conn.set_progress_handler(None, 0)
        truncated = len(rows) > self.max_rows
        return QueryResult(
            columns,
            tuple(tuple(r) for r in rows[: self.max_rows]),
            truncated,
            round((time.monotonic() - started) * 1000, 2),
        )

    def close(self) -> None:
        self._conn.close()


def load_catalog(path: str | Path) -> Catalog:
    """Introspect with a privileged connection (needed to see tables and sample values)."""
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} not found. Run `sqlscout init-db` first.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return introspect(conn)
    finally:
        conn.close()
