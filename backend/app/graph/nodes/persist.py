"""Persist node: the single write path at the end of every turn.

Writes user+assistant Message rows, bumps the session timestamp, fires the
auto-title task on first turns, and returns the accumulating `messages`
delta. DB failures are logged but never fail the turn (the user already has
their streamed answer).
"""

import uuid

from sqlalchemy import func, select

from app.ai.base import UserMessage as AiUserMessage
from app.ai.completion import complete
from app.core.logging import log
from app.core.prompt_registry import render_agent_prompt
from app.core.tasks import spawn
from app.db.session import SessionLocal
from app.graph.state import AssistantState
from app.models.chat_session import ChatSession
from app.models.message import Message
from app.models.user import utcnow

TITLE_MAX_CHARS = 60


async def _auto_title(session_id: uuid.UUID, first_message: str) -> None:
    """Fire-and-forget: name the thread from its first message."""
    try:
        title = await complete(
            [
                AiUserMessage(
                    content=render_agent_prompt("title_generator", message=first_message[:500])
                )
            ],
            temperature=0.2,
            max_tokens=24,
        )
        title = title.strip().strip('"')[:TITLE_MAX_CHARS] or first_message[:TITLE_MAX_CHARS]
    except Exception:  # noqa: BLE001 — fallback title, never fail
        title = first_message[:TITLE_MAX_CHARS]
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is not None and not session.title:
            session.title = title
            await db.commit()


async def persist_node(state: AssistantState) -> AssistantState:
    """Write the turn; always returns a message_id and the messages delta."""
    assistant_id = uuid.uuid4()
    session_uuid = uuid.UUID(state["session_id"])
    user_msg = state["user_msg"]
    final_text = state.get("final_text", "")
    try:
        async with SessionLocal() as db:
            db.add(Message(session_id=session_uuid, role="user", content=user_msg))
            db.add(
                Message(
                    id=assistant_id,
                    session_id=session_uuid,
                    role="assistant",
                    content=final_text,
                    route=state.get("route"),
                    tool_calls=state.get("tool_calls"),
                    citations=state.get("citations"),
                )
            )
            session = await db.get(ChatSession, session_uuid)
            if session is not None:
                session.updated_at = utcnow()
            count = await db.scalar(
                select(func.count()).select_from(Message).where(Message.session_id == session_uuid)
            )
            await db.commit()
        if count is not None and count <= 2:  # first turn just landed
            spawn(_auto_title(session_uuid, user_msg), name="chat.auto_title")
    except Exception as exc:  # noqa: BLE001 — never fail the turn on persistence
        log.error("node.persist.error", error=str(exc))
    return {
        "message_id": str(assistant_id),
        "messages": [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": final_text},
        ],
    }
