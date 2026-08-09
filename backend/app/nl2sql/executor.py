"""The 5-layer SQL defense + read-only execution.

1. sqlglot parse: exactly ONE statement, must be a SELECT (CTEs allowed)
2. AST walk: any write/DDL/admin expression anywhere in the tree -> reject
3. Table whitelist: every referenced table must be in ALLOWED_TABLES
4. LIMIT: injected if missing, clamped to MAX_ROWS
5. Read-only Postgres role (SELECT-on-fundamentals only, statement_timeout=5s)
   + an asyncio timeout on top — even a validator bypass cannot write.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from langsmith import traceable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlglot import exp

from app.core.config import settings
from app.nl2sql.schema import ALLOWED_TABLES

MAX_ROWS = 200
QUERY_TIMEOUT_S = 5.0

_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Grant,
    exp.TruncateTable,
)

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)

_ro_engine: AsyncEngine | None = None


def _get_ro_engine() -> AsyncEngine:
    global _ro_engine  # noqa: PLW0603 — lazy singleton
    if _ro_engine is None:
        _ro_engine = create_async_engine(settings.NL2SQL_RO_DATABASE_URL, pool_pre_ping=True)
    return _ro_engine


class SQLValidationError(Exception):
    """Raised when generated SQL fails any defense layer."""


def extract_sql(raw: str) -> str:
    """Pull SQL out of a fenced code block, else use the raw text."""
    match = _SQL_FENCE.search(raw)
    return (match.group(1) if match else raw).strip().rstrip(";")


def validate(sql: str) -> str:
    """Run layers 1-4; return the (possibly LIMIT-adjusted) SQL or raise."""
    if not sql.strip():
        raise SQLValidationError("empty SQL")
    try:
        parsed = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"unparseable SQL: {exc}") from exc
    statements = [s for s in parsed if s is not None]
    if len(statements) != 1:
        raise SQLValidationError("exactly one statement is allowed")
    stmt = statements[0]

    root = stmt
    while isinstance(root, exp.With):  # unwrap CTE wrapper
        root = root.this
    if not isinstance(root, exp.Select):
        raise SQLValidationError("only SELECT statements are allowed")

    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN):
            raise SQLValidationError(f"forbidden expression: {type(node).__name__}")
        # `SELECT ... INTO new_table` parses as a plain Select — catch the
        # INTO arg explicitly; it's a write wearing SELECT clothing.
        if isinstance(node, exp.Select) and node.args.get("into") is not None:
            raise SQLValidationError("SELECT INTO is not allowed")

    cte_names = {cte.alias_or_name for cte in stmt.find_all(exp.CTE)}
    referenced: set[str] = set()
    for table in stmt.find_all(exp.Table):
        name = table.name
        if name and name not in cte_names and name not in ALLOWED_TABLES:
            raise SQLValidationError(f"table not allowed: {name}")
        if name:
            referenced.add(name)
    # Table-free queries (SELECT pg_sleep(30), SELECT generate_series(...))
    # slipped every layer above — require at least one whitelisted table.
    if not referenced & ALLOWED_TABLES:
        raise SQLValidationError("query must read from an allowed table")

    # Apply the LIMIT on the (unwrapped) SELECT in place so it lands inside
    # the original statement tree even when the top node is a CTE wrapper.
    limit = root.args.get("limit")
    if limit is None:
        root.limit(MAX_ROWS, copy=False)
    else:
        try:
            current = int(limit.expression.name)
            if current > MAX_ROWS:
                root.limit(MAX_ROWS, copy=False)
        except (AttributeError, ValueError):
            root.limit(MAX_ROWS, copy=False)
    return stmt.sql(dialect="postgres")


@dataclass
class ExecutionResult:
    columns: list[str]
    rows: list[dict[str, Any]]


def shape_execution_output(result: "ExecutionResult") -> dict[str, Any]:
    """Trace-safe view of a result: counts only — never row payloads, never
    connection details (the RO DSN carries a password)."""
    return {"row_count": len(result.rows), "columns": result.columns}


@traceable(run_type="tool", name="sql.execute", process_outputs=shape_execution_output)
async def execute(validated_sql: str) -> ExecutionResult:
    """Layer 5: run on the read-only engine with a hard timeout."""

    async def _run() -> ExecutionResult:
        async with _get_ro_engine().connect() as conn:
            result = await conn.execute(text(validated_sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()[:MAX_ROWS]]
            return ExecutionResult(columns=columns, rows=rows)

    return await asyncio.wait_for(_run(), timeout=QUERY_TIMEOUT_S)
