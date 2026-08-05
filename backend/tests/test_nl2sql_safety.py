"""Sanity: the 5-layer NL2SQL guardrails (validation layers 1-4, no live DB)."""

import pytest

from app.nl2sql.executor import MAX_ROWS, SQLValidationError, extract_sql, validate

pytestmark = pytest.mark.sanity


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM fundamentals",
        "UPDATE fundamentals SET pe_ratio = 0",
        "INSERT INTO fundamentals (symbol) VALUES ('X')",
        "DROP TABLE fundamentals",
        "TRUNCATE fundamentals",
        "CREATE TABLE evil (id int)",
        "ALTER TABLE fundamentals ADD COLUMN evil int",
        "GRANT ALL ON fundamentals TO PUBLIC",
    ],
    ids=["delete", "update", "insert", "drop", "truncate", "create", "alter", "grant"],
)
def test_rejects_writes_and_ddl(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate(sql)


def test_rejects_multi_statement_injection() -> None:
    with pytest.raises(SQLValidationError, match="one statement"):
        validate("SELECT * FROM fundamentals; DROP TABLE users")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "SELECT * FROM messages",
        "SELECT * FROM watchlist_items",
        "SELECT f.symbol, u.email FROM fundamentals f JOIN users u ON true",
    ],
    ids=["users", "messages", "watchlist", "join-leak"],
)
def test_rejects_non_whitelisted_tables(sql: str) -> None:
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate(sql)


def test_rejects_empty_and_prose() -> None:
    with pytest.raises(SQLValidationError):
        validate("")
    with pytest.raises(SQLValidationError):
        validate("please show me some stocks")


def test_accepts_simple_select_and_injects_limit() -> None:
    out = validate("SELECT symbol FROM fundamentals WHERE pe_ratio < 20")
    assert f"LIMIT {MAX_ROWS}" in out


def test_accepts_cte() -> None:
    out = validate("WITH cheap AS (SELECT * FROM fundamentals) SELECT * FROM cheap")
    assert "LIMIT" in out


def test_clamps_oversized_limit() -> None:
    out = validate("SELECT * FROM fundamentals LIMIT 99999")
    assert f"LIMIT {MAX_ROWS}" in out


def test_keeps_small_limit() -> None:
    out = validate("SELECT * FROM fundamentals LIMIT 5")
    assert "LIMIT 5" in out


def test_extract_sql_from_fence() -> None:
    raw = "Here you go:\n```sql\nSELECT symbol FROM fundamentals\n```"
    assert extract_sql(raw) == "SELECT symbol FROM fundamentals"


def test_extract_sql_plain() -> None:
    assert extract_sql("SELECT 1;") == "SELECT 1"
