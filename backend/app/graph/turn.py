"""Turn runner: one user message in, one persisted assistant reply out.

Now graph-backed: supervisor routing across all six specialists, Postgres
checkpointing (thread_id = session_id), and per-turn state reset so stale
citations/tool-calls never leak across turns. The WS layer's contract is
unchanged from the v1 single-node era.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.logging import log, new_correlation_id
from app.db.session import SessionLocal
from app.graph.graph import get_graph
from app.graph.state import AssistantState
from app.models.message import Message

HISTORY_RELOAD_LIMIT = 24  # degraded-mode (no checkpointer) history window


async def _load_history(session_id: uuid.UUID) -> list[dict[str, Any]]:
    """DB fallback history for the no-checkpointer degrade path."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(HISTORY_RELOAD_LIMIT)
        )
        rows = list(result.scalars())
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def run_turn(
    session_id: uuid.UUID, user_id: uuid.UUID, user_name: str, user_msg: str
) -> dict[str, Any]:
    """Execute one turn through the graph; returns the WS response payload."""
    cid = new_correlation_id()
    graph, has_checkpointer = await get_graph()
    turn_input: AssistantState = {
        "session_id": str(session_id),
        "user_id": str(user_id),
        "user_name": user_name,
        "user_msg": user_msg,
        "correlation_id": cid,
        # explicit per-turn resets — only `messages` may accumulate
        "route": "",
        "plan": [],
        "route_reason": "",
        "final_text": "",
        "citations": [],
        "tool_calls": [],
        "actions": [],
        "data_as_of": "",
        "sources": [],
        "message_id": "",
    }
    if not has_checkpointer:
        turn_input["messages"] = await _load_history(session_id)
    config = {"configurable": {"thread_id": str(session_id)}}
    out: AssistantState = await graph.ainvoke(turn_input, config=config)
    log.info("turn.done", session_id=str(session_id), route=out.get("route"))
    return {
        "type": "response",
        "message_id": out.get("message_id", ""),
        "message": out.get("final_text", ""),
        "route": out.get("route", "general_chat"),
        "route_reason": out.get("route_reason", ""),
        "citations": out.get("citations", []),
        "tool_calls": out.get("tool_calls", []),
        "actions": out.get("actions", []),
        "data_as_of": out.get("data_as_of") or datetime.now(UTC).isoformat(),
        "sources": out.get("sources", []),
        "session_id": str(session_id),
    }
