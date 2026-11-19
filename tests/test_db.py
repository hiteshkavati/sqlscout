import sqlite3

import pytest

from sqlscout.db import Database, DatabaseError


@pytest.fixture()
def db(db_path, policy):
    d = Database(db_path, policy, max_rows=50, timeout_s=0.3)
    yield d
    d.close()


def test_runs_select(db):
    r = db.execute("SELECT COUNT(*) AS n FROM orders")
    assert r.columns == ("n",) and r.rows[0][0] == 12000 and not r.truncated


def test_rows_are_capped_and_flagged(db):
    r = db.execute("SELECT * FROM orders")
    assert len(r.rows) == 50 and r.truncated


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET total_inr = 0",
        "DROP TABLE orders",
        "INSERT INTO products VALUES (999,'x','y',1,1)",
        "CREATE TABLE t(a)",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE ':memory:' AS m",
    ],
)
def test_database_layer_refuses_writes_even_without_the_guard(db, sql):
    with pytest.raises(DatabaseError):
        db.execute(sql)


def test_database_layer_denies_hidden_column(db):
    with pytest.raises(DatabaseError):
        db.execute("SELECT email FROM customers")


def test_database_layer_denies_system_tables_and_functions(db):
    with pytest.raises(DatabaseError):
        db.execute("SELECT name FROM sqlite_master")
    with pytest.raises(DatabaseError):
        db.execute("SELECT load_extension('x')")
    with pytest.raises(DatabaseError):
        db.execute("SELECT sqlite_version()")


def test_timeout_interrupts_runaway_query(db):
    with pytest.raises(DatabaseError, match="time limit"):
        db.execute("SELECT COUNT(*) FROM orders a, orders b, orders c")


def test_data_unchanged_after_attempts(db, db_path):
    for sql in ("DELETE FROM orders", "DROP TABLE orders"):
        with pytest.raises(DatabaseError):
            db.execute(sql)
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 12000


def test_missing_database_file(policy, tmp_path):
    with pytest.raises(FileNotFoundError):
        Database(tmp_path / "nope.db", policy)


def test_concurrent_queries_do_not_interfere(db):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        results = list(
            pool.map(lambda _: db.execute("SELECT COUNT(*) FROM orders").rows[0][0], range(24))
        )
    assert set(results) == {12000}
