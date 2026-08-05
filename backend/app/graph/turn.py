"""Turn runner: one user message in, one persisted assistant reply out.

v1 sends every turn to `general_chat`; the supervisor graph replaces the
routing internals later without changing this function's contract, so the WS
layer never needs to know which era it's running in.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.ai.base import UserMessage as AiUserMessage
from app.ai.completion import complete
from app.core.logging import log, new_correlation_id
from app.core.prompt_registry import render_agent_prompt
from app.db.session import SessionLocal
from app.graph.nodes.general_chat import general_chat_node
from app.graph.state import AssistantState
from app.models.chat_session import ChatSession
from app.models.message import Message

TITLE_MAX_CHARS = 60


async def _load_history(session_id: uuid.UUID) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        return [{"role": m.role, "content": m.content} for m in result.scalars()]


async def _persist_turn(
    session_id: uuid.UUID, user_msg: str, out: AssistantState
) -> uuid.UUID:
    """Write user+assistant rows; return the assistant message id."""
    assistant_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(Message(session_id=session_id, role="user", content=user_msg))
        db.add(
            Message(
                id=assistant_id,
                session_id=session_id,
                role="assistant",
                content=out.get("final_text", ""),
                route=out.get("route"),
                tool_calls=out.get("tool_calls"),
                citations=out.get("citations"),
            )
        )
        session = await db.get(ChatSession, session_id)
        if session is not None:
            from app.models.user import utcnow

            session.updated_at = utcnow()
        await db.commit()
    return assistant_id


async def _auto_title(session_id: uuid.UUID, first_message: str) -> None:
    """Fire-and-forget: name the thread from its first message."""
    try:
        title = await complete(
            [AiUserMessage(content=render_agent_prompt("title_generator", message=first_message[:500]))],
            temperature=0.2,
            max_tokens=24,
        )
        title = title.strip().strip('"')[:TITLE_MAX_CHARS] or first_message[:TITLE_MAX_CHARS]
    except Exception:  # noqa: BLE001 — fallback title, never fail the turn
        title = first_message[:TITLE_MAX_CHARS]
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is not None and not session.title:
            session.title = title
            await db.commit()


async def run_turn(
    session_id: uuid.UUID, user_id: uuid.UUID, user_name: str, user_msg: str
) -> dict[str, Any]:
    """Execute one turn and return the final response payload for the WS frame."""
    cid = new_correlation_id()
    history = await _load_history(session_id)
    state: AssistantState = {
        "session_id": str(session_id),
        "user_id": str(user_id),
        "user_name": user_name,
        "user_msg": user_msg,
        "correlation_id": cid,
        "messages": history,
    }
    out = await general_chat_node(state)
    assistant_id = await _persist_turn(session_id, user_msg, out)
    if not history:
        _task = asyncio.create_task(_auto_title(session_id, user_msg))  # noqa: RUF006
    log.info("turn.done", session_id=str(session_id), route=out.get("route"))
    return {
        "type": "response",
        "message_id": str(assistant_id),
        "message": out.get("final_text", ""),
        "route": out.get("route", "general_chat"),
        "route_reason": out.get("route_reason", ""),
        "citations": out.get("citations", []),
        "tool_calls": out.get("tool_calls", []),
        "actions": out.get("actions", []),
        "data_as_of": out.get("data_as_of", datetime.now(UTC).isoformat()),
        "sources": out.get("sources", []),
        "session_id": str(session_id),
    }
