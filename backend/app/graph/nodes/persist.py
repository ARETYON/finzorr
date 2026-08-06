"""Persist node: the single write path at the end of every turn.

Writes user+assistant Message rows, bumps the session timestamp, fires the
auto-title task on first turns, and returns the accumulating `messages`
delta. DB failures are logged but never fail the turn (the user already has
their streamed answer).
"""

import uuid

from langsmith import traceable
from sqlalchemy import func, select
from sqlalchemy.exc import InterfaceError, OperationalError

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


@traceable(run_type="chain", name="chat.auto_title")
async def _auto_title(session_id: uuid.UUID, first_message: str) -> None:
    """Fire-and-forget: name the thread from its first message. Traceable so
    it's a NAMED root when spawned out-of-band (and cleanly separated when
    the spawning persist node's run has already ended)."""
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


async def _already_persisted(turn_id: str) -> bool:
    """Idempotency check — the marker is set only AFTER a successful commit,
    so a graph-level retry of a failed write is never suppressed, while a
    cancel/timeout racing a COMPLETED persist is. Best-effort (fails open)."""
    if not turn_id:
        return False
    try:
        from app.core.redis import get_redis

        return bool(await get_redis().get(f"persisted:{turn_id}"))
    except Exception:  # noqa: BLE001 — idempotency is best-effort
        return False


async def _mark_persisted(turn_id: str) -> None:
    if not turn_id:
        return
    try:
        from app.core.redis import get_redis

        await get_redis().set(f"persisted:{turn_id}", "1", ex=3600)
    except Exception:  # noqa: BLE001
        log.warning("node.persist.mark_failed")


async def persist_node(state: AssistantState) -> AssistantState:
    """Write the turn; returns a message_id and the messages delta.

    Transient DB errors (OperationalError/InterfaceError) RE-RAISE so the
    graph-level RetryPolicy gets a real second attempt; anything else
    degrades (the user already has their streamed answer).
    """
    assistant_id = uuid.uuid4()
    session_uuid = uuid.UUID(state["session_id"])
    user_msg = state["user_msg"]
    final_text = state.get("final_text", "")
    turn_id = state.get("turn_id", "")
    if await _already_persisted(turn_id):
        log.info("node.persist.duplicate_suppressed", session_id=str(session_uuid))
        return {"message_id": "", "messages": []}
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
        await _mark_persisted(turn_id)
        if count is not None and count <= 2:  # first turn just landed
            spawn(_auto_title(session_uuid, user_msg), name="chat.auto_title")
    except (OperationalError, InterfaceError):
        raise  # transient — let the graph's RetryPolicy take the second shot
    except Exception as exc:  # noqa: BLE001 — never fail the turn on persistence
        log.error("node.persist.error", error=str(exc))
    return {
        "message_id": str(assistant_id),
        "messages": [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": final_text},
        ],
    }
