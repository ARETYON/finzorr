"""NL2SQL route: screener questions over the fundamentals table."""

from datetime import UTC, datetime

from app.ai.base import SystemMessage, UserMessage
from app.ai.completion import stream
from app.core.logging import log
from app.core.prompt_registry import render_agent_prompt
from app.graph.nodes.common import step_context, task_for, with_instructions
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame
from app.nl2sql.agent import rows_preview, run_query

STALE_EMPTY_HINT = (
    "The screener database appears to be empty — the daily fundamentals refresh "
    "may not have run yet."
)


async def nl2sql_node(state: AssistantState) -> AssistantState:
    """Guarded SQL screening; the executed SQL is surfaced as a citation."""
    result = await run_query(task_for(state))
    if not result.success:
        log.warning("node.nl2sql.failed", error=result.error)
        return {
            "final_text": (
                "I couldn't turn that into a valid screener query. "
                "Try rephrasing — e.g. \"NSE stocks with P/E under 20 and dividend "
                "yield above 2%\"."
            ),
            "route": "nl2sql",
            "step_error": True,
            "tool_calls": [
                {"name": "nl2sql", "arguments": {}, "result": result.error[:300]}
            ],
        }

    assert result.result is not None
    rows = rows_preview(result.result)

    async def on_token(t: str) -> None:
        # inside a Send fan-out, concurrent branch streams would interleave
        # into one garbled bubble — compose streams the visible answer
        if not state.get("parallel_branch", False):
            emit_frame({"type": "token", "delta": t})

    try:
        prompt = render_agent_prompt(
            "nl2sql_answer",
            question=task_for(state) + step_context(state),
            row_count=str(len(result.result.rows)),
            rows=str(rows) if rows else f"[] ({STALE_EMPTY_HINT})",
        )
        done = await stream(
            [
                SystemMessage(
                    content=with_instructions("You are a precise data narrator.", state)
                ),
                UserMessage(content=prompt),
            ],
            on_token=on_token,
            temperature=0.2,
            max_tokens=1024,
        )
        narration = done.text
    except Exception as exc:  # noqa: BLE001 — degrade to raw rows
        log.error("node.nl2sql.narration_error", error=str(exc))
        narration = f"Query returned {len(result.result.rows)} rows: {rows}"

    return {
        "final_text": narration,
        "route": "nl2sql",
        "citations": [{"marker": "SQL", "title": "Executed query", "snippet": result.sql}],
        "data_as_of": datetime.now(UTC).isoformat(),
        "sources": ["finzorr screener (daily refresh)"],
    }
