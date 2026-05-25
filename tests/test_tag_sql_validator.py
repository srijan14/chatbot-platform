"""Coverage for the sqlglot-based SQL safety validator."""
from __future__ import annotations

import pytest

from src.chatbot.engines.tag_engine.sql_validator import validate_and_prepare


def _ok(sql: str, **kw):
    r = validate_and_prepare(sql, **kw)
    assert r.ok, f"expected ok, got {r.reason!r}"
    return r


def _nok(sql: str, **kw) -> str:
    r = validate_and_prepare(sql, **kw)
    assert not r.ok, f"expected reject, got accepted: {r.sql!r}"
    assert r.reason is not None
    return r.reason


def test_simple_select_passes():
    r = _ok("SELECT 1")
    assert "SELECT 1" in r.sql


def test_limit_injected_when_missing():
    r = _ok("SELECT * FROM customers", row_limit=50)
    assert "LIMIT 50" in r.sql.upper()


def test_existing_limit_preserved():
    r = _ok("SELECT * FROM customers LIMIT 5", row_limit=50)
    # Validator must not overwrite an explicit LIMIT (and must not double it up).
    upper = r.sql.upper()
    assert "LIMIT 5" in upper
    assert "LIMIT 50" not in upper


def test_with_cte_passes_and_limits_outer_select():
    r = _ok("WITH t AS (SELECT 1 AS x) SELECT * FROM t", row_limit=10)
    assert "LIMIT 10" in r.sql.upper()


def test_union_passes():
    _ok("SELECT 1 UNION SELECT 2", row_limit=10)


def test_multiple_statements_rejected():
    assert "multiple statements" in _nok("SELECT 1; SELECT 2").lower()


def _assert_blocked(reason: str):
    """The validator may reject at the top-level kind check OR the inner walk.
    Either result keeps mutations out — caller just needs to know it was rejected.
    """
    lower = reason.lower()
    assert "disallowed" in lower or "top-level" in lower, f"unexpected reason: {reason!r}"


def test_insert_rejected():
    _assert_blocked(_nok("INSERT INTO t VALUES (1)"))


def test_delete_rejected():
    _assert_blocked(_nok("DELETE FROM customers"))


def test_update_rejected():
    _assert_blocked(_nok("UPDATE customers SET segment='free'"))


def test_drop_rejected():
    _assert_blocked(_nok("DROP TABLE customers"))


def test_pragma_rejected():
    _assert_blocked(_nok("PRAGMA table_list"))


def test_create_table_as_select_rejected():
    """The canonical sneak-attack — SELECT inside a CREATE."""
    _assert_blocked(_nok("CREATE TABLE x AS SELECT * FROM customers"))


def test_alter_rejected():
    _assert_blocked(_nok("ALTER TABLE customers ADD COLUMN evil TEXT"))


def test_attach_rejected():
    _assert_blocked(_nok('ATTACH DATABASE "x.db" AS db2'))


def test_parse_error_rejected():
    assert "parse" in _nok("SELECT FROM WHERE").lower()


def test_empty_input_rejected():
    assert "empty" in _nok("").lower()
    assert "empty" in _nok("   ").lower()
