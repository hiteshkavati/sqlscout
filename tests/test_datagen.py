import hashlib
import sqlite3

from sqlscout.datagen import N_CUSTOMERS, N_ORDERS, build


def _digest(path):
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for table in ("customers", "products", "orders", "order_items", "returns"):
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
            h.update(repr(row).encode())
    return h.hexdigest()


def test_same_seed_gives_identical_data(tmp_path):
    a = build(tmp_path / "a.db", seed=7)
    b = build(tmp_path / "b.db", seed=7)
    assert _digest(a) == _digest(b)


def test_different_seed_changes_data(tmp_path):
    assert _digest(build(tmp_path / "a.db", seed=1)) != _digest(build(tmp_path / "b.db", seed=2))


def test_shape_and_referential_integrity(db_path):
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == N_CUSTOMERS
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == N_ORDERS
    orphans = conn.execute(
        "SELECT COUNT(*) FROM order_items oi LEFT JOIN orders o USING(order_id) "
        "WHERE o.order_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    statuses = {r[0] for r in conn.execute("SELECT DISTINCT status FROM orders")}
    assert statuses == {"delivered", "cancelled", "returned", "shipped"}


def test_order_total_matches_items_minus_discount(db_path):
    conn = sqlite3.connect(db_path)
    bad = conn.execute(
        "SELECT COUNT(*) FROM orders o JOIN (SELECT order_id, SUM(quantity*unit_price_inr) s "
        "FROM order_items GROUP BY order_id) i USING(order_id) "
        "WHERE ABS(o.total_inr - (i.s - o.discount_inr)) > 0.01"
    ).fetchone()[0]
    assert bad == 0


def test_emails_use_reserved_example_domain(db_path):
    conn = sqlite3.connect(db_path)
    emails = [r[0] for r in conn.execute("SELECT email FROM customers LIMIT 50")]
    assert all(e.endswith("@example.com") for e in emails)
