"""Schema introspection and the policy derived from it."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .guard import Policy

# Columns a model must never read. They exist in the database but are hidden from the
# schema shown to the model and rejected by the guard and by the database authorizer.
DEFAULT_RESTRICTED = {"customers": frozenset({"email"})}

DEFAULT_DESCRIPTIONS = {
    "customers": "One row per customer.",
    "products": "Product catalogue with list price and unit cost.",
    "orders": "One row per order. Dates are ISO text (YYYY-MM-DD).",
    "order_items": "Line items. quantity * unit_price_inr is the line value before order discount.",
    "returns": "One row per returned line item.",
    "orders.status": "delivered, cancelled, returned or shipped.",
    "orders.total_inr": "Order value after discount, in rupees.",
    "orders.delivery_city": "City the order was delivered to (may differ from the customer city).",
    "orders.discount_inr": "Discount applied to the whole order, in rupees.",
    "customers.segment": "new, regular or premium.",
    "products.cost_inr": "What the product costs the seller; margin = price_inr - cost_inr.",
    "returns.refund_inr": "Amount refunded for the returned line.",
}

_SAMPLE_COLUMNS = {
    ("customers", "city"),
    ("customers", "state"),
    ("customers", "segment"),
    ("products", "category"),
    ("orders", "status"),
    ("orders", "payment_method"),
    ("orders", "delivery_city"),
    ("returns", "reason"),
}


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str = ""
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    rows: int
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class Catalog:
    tables: tuple[Table, ...]
    restricted: dict[str, frozenset[str]]

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)

    def policy(self, default_limit: int = 100, max_limit: int = 200) -> Policy:
        return Policy(
            tables={t.name: tuple(c.name for c in t.columns) for t in self.tables},
            restricted=self.restricted,
            default_limit=default_limit,
            max_limit=max_limit,
        )

    def describe(self) -> str:
        lines = []
        for t in self.tables:
            lines.append(f"{t.name} ({t.rows} rows): {t.description}")
            for c in t.columns:
                extra = f" - {c.description}" if c.description else ""
                vals = f" Values: {', '.join(c.samples)}." if c.samples else ""
                lines.append(f"  {c.name} {c.type}{extra}{vals}")
        return "\n".join(lines)


def introspect(
    conn: sqlite3.Connection,
    restricted: dict[str, frozenset[str]] | None = None,
    descriptions: dict[str, str] | None = None,
) -> Catalog:
    restricted = DEFAULT_RESTRICTED if restricted is None else restricted
    descriptions = DEFAULT_DESCRIPTIONS if descriptions is None else descriptions
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    tables = []
    for name in names:
        hidden = {c.lower() for c in restricted.get(name, ())}
        cols = []
        for _, col, ctype, *_ in conn.execute(f'PRAGMA table_info("{name}")'):
            if col.lower() in hidden:
                continue
            samples: tuple[str, ...] = ()
            if (name, col) in _SAMPLE_COLUMNS:
                rows = conn.execute(
                    f'SELECT DISTINCT "{col}" FROM "{name}" ORDER BY 1 LIMIT 15'
                ).fetchall()
                samples = tuple(str(r[0]) for r in rows)
            cols.append(Column(col, ctype or "", descriptions.get(f"{name}.{col}", ""), samples))
        count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        tables.append(Table(name, descriptions.get(name, ""), count, tuple(cols)))
    return Catalog(tuple(tables), {k: frozenset(v) for k, v in restricted.items()})
