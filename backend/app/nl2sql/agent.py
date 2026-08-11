"""NL2SQL agent: generate -> validate -> execute, with ONE self-correction retry.

On any validation/execution failure the error and failed SQL are fed back to
the model for exactly one second attempt — more rounds add latency for
diminishing returns.
"""

from dataclasses import dataclass, field
from typing import Any

from langsmith import traceable

from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.infrastructure.llm.base import SystemMessage, UserMessage
from app.infrastructure.llm.completion import complete
from app.nl2sql.executor import (
    ExecutionResult,
    extract_sql,
    validate,
)
from app.nl2sql.executor import execute as execute_sql
from app.nl2sql.schema import schema_summary

MAX_ATTEMPTS = 2
NARRATION_ROWS = 30

register(
    AgentPrompt(
        name="nl2sql_generator",
        version="2",
        template=(
            "You translate questions about Indian stocks into PostgreSQL SELECT queries.\n"
            "Schema (the ONLY table you may query):\n{schema}\n\n"
            "Rules:\n"
            "- Output ONLY the SQL inside a ```sql fenced block. No prose.\n"
            "- PostgreSQL dialect ONLY: use to_char/date_trunc, never strftime; "
            "ILIKE is fine.\n"
            "- SELECT only. Exactly one statement. Always include LIMIT (max 200).\n"
            "- market_cap is in INR. dividend_yield is a PERCENTAGE (5.5 = 5.5%). "
            "roe is a ratio (0.29 = 29%).\n"
            "- Filter out NULLs on columns you sort by.\n"
            "{error_hint}"
        ),
    )
)

register(
    AgentPrompt(
        name="nl2sql_answer",
        version="1",
        template=(
            "Answer the user's question in 1-3 sentences grounded ONLY in these query "
            "results (never invent numbers), then include a compact markdown table of "
            "the rows if more than one.\nQuestion: {question}\nResults ({row_count} "
            "rows): {rows}\nEnd with: \"Screener data is refreshed daily — not "
            "investment advice.\""
        ),
    )
)


@dataclass
class NL2SQLResult:
    success: bool
    sql: str = ""
    result: ExecutionResult | None = None
    error: str = ""
    attempts: int = 0
    trace: list[str] = field(default_factory=list)


def rows_preview(result: ExecutionResult) -> list[dict[str, Any]]:
    """Cap rows fed into the narration prompt."""
    return result.rows[:NARRATION_ROWS]


@traceable(run_type="chain", name="nl2sql.agent")
async def run_query(question: str) -> NL2SQLResult:
    """Generate + validate + execute with one error-fed retry. Never raises."""
    from app.core.trace import mark
    error_hint = ""
    last_sql = ""
    error = "no attempt completed"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # Generation sits INSIDE the try: an LLM outage must degrade to
            # success=False like every other failure, not escape the node.
            prompt = render_agent_prompt(
                "nl2sql_generator", schema=schema_summary(), error_hint=error_hint
            )
            raw = await complete(
                [SystemMessage(content=prompt), UserMessage(content=question)],
                temperature=0.1,
                max_tokens=512,
            )
            last_sql = extract_sql(raw)
            validated = validate(last_sql)
            result = await execute_sql(validated)
            log.info("nl2sql.success", attempt=attempt, rows=len(result.rows))
            mark(attempts=attempt, self_corrected=attempt > 1, success=True)
            return NL2SQLResult(success=True, sql=validated, result=result, attempts=attempt)
        except Exception as exc:  # noqa: BLE001 — includes SQLValidationError
            error = f"{type(exc).__name__}: {exc}"
            log.warning("nl2sql.attempt_failed", attempt=attempt, error=error)
            error_hint = (
                f"\nYour previous attempt failed.\nPrevious SQL: {last_sql}\n"
                f"Error: {error}\nFix the problem and try again."
            )
    mark(attempts=MAX_ATTEMPTS, success=False, last_error=error[:200])
    return NL2SQLResult(success=False, sql=last_sql, error=error, attempts=MAX_ATTEMPTS)
