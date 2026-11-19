"""Validation of model-written SQL.

The query is parsed into an AST, checked against a policy, rewritten (star expansion,
LIMIT) and then regenerated as SQL. The regenerated text is what gets executed, so what was
validated is exactly what runs. Database-level protections in `db.py` back this up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

# sqlglot logs a warning whenever it falls back to a generic "Command" node; we reject those
# statements anyway, so the message is just noise.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

MAX_SQL_CHARS = 4000

# Statement and clause types that must never appear anywhere in the tree.
_FORBIDDEN_NAMES = (
    # functions that reveal engine or session details
    "CurrentVersion",
    "CurrentUser",
    "CurrentCatalog",
    "CurrentSchema",
    "CurrentSchemas",
    "SessionUser",
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",
    "AlterColumn",
    "Command",
    "Pragma",
    "Attach",
    "Detach",
    "Merge",
    "Transaction",
    "Commit",
    "Rollback",
    "Set",
    "Use",
    "Into",
    "TruncateTable",
    "Copy",
    "LoadData",
    "Grant",
    "Analyze",
    "Kill",
)
FORBIDDEN_NODES: tuple[type, ...] = tuple(
    t for n in _FORBIDDEN_NAMES if isinstance(t := getattr(exp, n, None), type)
)

# Functions sqlglot does not model itself ("Anonymous") are only allowed if listed here.
# Everything else that sqlglot knows (SUM, COUNT, CASE, CAST, window functions, ...) is pure.
ALLOWED_ANONYMOUS = frozenset(
    {
        "strftime",
        "date",
        "datetime",
        "time",
        "julianday",
        "unixepoch",
        "ifnull",
        "iif",
        "printf",
        "instr",
        "typeof",
        "total",
        "hex",
        "quote",
        "unicode",
        "char",
        "round",
        "substr",
        "substring",
        "replace",
        "trim",
        "ltrim",
        "rtrim",
        "lower",
        "upper",
        "length",
        "abs",
        "max",
        "min",
        "coalesce",
        "nullif",
        "group_concat",
        "glob",
        "like",
        "sign",
        "likely",
        "unlikely",
    }
)


class SQLRejected(Exception):
    """Raised when a query is refused. `kind` says why; `message` is safe to show the model."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "unsafe" | "policy" | "invalid"
        self.message = message


@dataclass(frozen=True)
class Policy:
    tables: dict[str, tuple[str, ...]]  # table -> visible columns
    restricted: dict[str, frozenset[str]] = field(default_factory=dict)  # hidden columns
    default_limit: int = 100
    max_limit: int = 200

    def schema_for_qualify(self) -> dict[str, dict[str, str]]:
        return {t: {c: "TEXT" for c in cols} for t, cols in self.tables.items()}

    def restricted_names(self) -> set[str]:
        return {c for cols in self.restricted.values() for c in cols}


@dataclass(frozen=True)
class Checked:
    sql: str
    tables: tuple[str, ...]
    notes: tuple[str, ...] = ()


def validate(sql: str, policy: Policy) -> Checked:
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SQLRejected("invalid", "Empty query.")
    if len(text) > MAX_SQL_CHARS:
        raise SQLRejected("policy", f"Query is longer than {MAX_SQL_CHARS} characters.")

    try:
        statements = [s for s in sqlglot.parse(text, read="sqlite") if s is not None]
    except ParseError as e:
        raise SQLRejected("invalid", f"SQL syntax error: {_short(e)}") from None
    except Exception as e:  # tokenizer errors
        raise SQLRejected("invalid", f"Could not parse the query: {_short(e)}") from None
    if len(statements) != 1:
        raise SQLRejected("unsafe", "Only a single statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, exp.Select | exp.SetOperation | exp.Subquery):
        raise SQLRejected("unsafe", "Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLRejected("unsafe", f"{type(node).__name__.upper()} is not allowed.")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SQLRejected("policy", "Recursive CTEs are not allowed.")
        if isinstance(node, exp.Anonymous):
            name = str(node.this).lower()
            if name not in ALLOWED_ANONYMOUS:
                raise SQLRejected("unsafe", f"Function {name}() is not allowed.")

    _check_tables(tree, policy)

    try:
        tree = qualify(
            tree,
            schema=policy.schema_for_qualify(),
            dialect="sqlite",
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except OptimizeError as e:
        kind, message = _column_message(str(e), policy)
        raise SQLRejected(kind, message) from None
    except Exception as e:
        raise SQLRejected("invalid", f"Could not resolve the query: {_short(e)}") from None

    _check_columns(tree, policy)
    tree, notes = _apply_limit(tree, policy)
    tables = tuple(
        sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name} & set(policy.tables))
    )
    return Checked(tree.sql(dialect="sqlite"), tables, tuple(notes))


def _check_tables(tree: exp.Expression, policy: Policy) -> None:
    ctes = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    allowed = {t.lower() for t in policy.tables}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLRejected("unsafe", "Table-valued functions are not allowed.")
        if table.args.get("db") or table.args.get("catalog"):
            raise SQLRejected("unsafe", "Schema-qualified table names are not allowed.")
        name = table.name.lower()
        if name in ctes:
            continue
        if name not in allowed:
            if name.startswith("sqlite_"):
                raise SQLRejected("unsafe", "System tables are not accessible.")
            raise SQLRejected(
                "policy",
                f"Table '{table.name}' is not available. Available tables: "
                f"{', '.join(sorted(policy.tables))}.",
            )


def _check_columns(tree: exp.Expression, policy: Policy) -> None:
    hidden = {(t.lower(), c.lower()) for t, cols in policy.restricted.items() for c in cols}
    for col in tree.find_all(exp.Column):
        if (col.table.lower(), col.name.lower()) in hidden:
            raise SQLRejected("policy", f"Column {col.table}.{col.name} is restricted.")


def _column_message(raw: str, policy: Policy) -> tuple[str, str]:
    for t, cols in policy.restricted.items():
        for c in cols:
            if f"'{c}'" in raw or f"{c} " in raw or raw.endswith(c):
                return "policy", f"Column {t}.{c} is restricted and cannot be queried."
    return "invalid", f"Unknown column or ambiguous reference ({_short(raw)})."


def _apply_limit(tree: exp.Expression, policy: Policy) -> tuple[exp.Expression, list[str]]:
    notes: list[str] = []
    if isinstance(tree, exp.SetOperation | exp.Subquery):
        tree = exp.select("*").from_(
            tree.subquery("q") if not isinstance(tree, exp.Subquery) else tree
        )
    limit = tree.args.get("limit")
    if limit is None:
        notes.append(f"LIMIT {policy.default_limit} added")
        return tree.limit(policy.default_limit), notes
    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        raise SQLRejected("policy", "LIMIT must be a plain integer.")
    if int(value.name) > policy.max_limit:
        notes.append(f"LIMIT lowered to {policy.max_limit}")
        return tree.limit(policy.max_limit), notes
    return tree, notes


def _short(err: object) -> str:
    text = str(err).strip().splitlines()[0] if str(err).strip() else "unknown error"
    return text[:160]
