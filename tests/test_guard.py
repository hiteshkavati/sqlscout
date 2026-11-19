import json

import pytest

from sqlscout.guard import SQLRejected, validate

from .conftest import ROOT

CASES = [json.loads(line) for line in (ROOT / "eval" / "attacks.jsonl").read_text().splitlines()]
ATTACKS = [c for c in CASES if c["expect"] == "block"]
LEGIT = [c for c in CASES if c["expect"] == "allow"]


@pytest.mark.parametrize("case", ATTACKS, ids=lambda c: c["sql"][:50] or "<empty>")
def test_attack_is_blocked(case, policy):
    with pytest.raises(SQLRejected):
        validate(case["sql"], policy)


@pytest.mark.parametrize("case", LEGIT, ids=lambda c: c["sql"][:50])
def test_legitimate_query_is_allowed(case, policy):
    assert validate(case["sql"], policy).sql


def test_rejection_kinds(policy):
    kinds = {
        "DROP TABLE orders": "unsafe",
        "SELECT * FROM users": "policy",
        "SELECT email FROM customers": "policy",
        "SELEC 1": "invalid",
        "SELECT nope FROM orders": "invalid",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t) SELECT n FROM t": "policy",
    }
    for sql, kind in kinds.items():
        with pytest.raises(SQLRejected) as info:
            validate(sql, policy)
        assert info.value.kind == kind, sql


def test_star_expands_without_restricted_columns(policy):
    sql = validate("SELECT * FROM customers", policy).sql.lower()
    assert "email" not in sql
    assert "customer_id" in sql and "segment" in sql


def test_default_limit_added(policy):
    checked = validate("SELECT * FROM orders", policy)
    assert checked.sql.upper().endswith(f"LIMIT {policy.default_limit}")
    assert checked.notes


def test_large_limit_is_lowered(policy):
    checked = validate("SELECT * FROM orders LIMIT 99999", policy)
    assert checked.sql.upper().endswith(f"LIMIT {policy.max_limit}")


def test_small_limit_is_kept(policy):
    checked = validate("SELECT * FROM orders LIMIT 5", policy)
    assert checked.sql.upper().endswith("LIMIT 5")
    assert not checked.notes


def test_union_is_wrapped_so_limit_applies(policy):
    sql = validate("SELECT city FROM customers UNION SELECT delivery_city FROM orders", policy).sql
    assert sql.upper().startswith("SELECT * FROM (")
    assert "LIMIT" in sql.upper()


def test_trailing_semicolon_and_whitespace_ok(policy):
    assert validate("  SELECT COUNT(*) FROM orders ;  ", policy).sql


def test_cte_name_is_not_treated_as_unknown_table(policy):
    sql = "WITH m AS (SELECT customer_id FROM orders) SELECT COUNT(*) FROM m"
    assert validate(sql, policy).tables == ("orders",)


def test_comments_cannot_hide_a_second_statement(policy):
    with pytest.raises(SQLRejected):
        validate("SELECT 1 /* ok */; DROP TABLE orders -- done", policy)


def test_mixed_case_keywords_and_names(policy):
    with pytest.raises(SQLRejected):
        validate("dRoP tAbLe Orders", policy)
    with pytest.raises(SQLRejected):
        validate("SELECT EMAIL FROM CUSTOMERS", policy)


def test_overlong_query_rejected(policy):
    with pytest.raises(SQLRejected):
        validate("SELECT " + ", ".join(["1"] * 3000), policy)


def test_executed_sql_is_the_validated_tree_not_the_input(policy):
    checked = validate("select  COUNT( * )   from   orders", policy)
    assert checked.sql.startswith("SELECT COUNT(*)")
