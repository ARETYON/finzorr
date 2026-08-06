"""Turn runner: one user message in, one persisted assistant reply out.

Now graph-backed: supervisor routing across all six specialists, Postgres
checkpointing (thread_id = session_id), and per-turn state reset so stale
citations/tool-calls never leak across turns. The WS layer's contract is
unchanged from the v1 single-node era.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.logging import log, new_correlation_id
from app.db.session import SessionLocal
from app.graph.graph import get_graph
from app.graph.state import AssistantState
from app.models.message import Message
from app.models.user import User

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


async def _load_instructions(user_id: uuid.UUID) -> str:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        return (user.custom_instructions or "") if user else ""


async def _vision_turn(
    session_id: uuid.UUID, user_id: uuid.UUID, user_msg: str, attachments: list[str]
) -> dict[str, Any]:
    """Answer a question about an uploaded image, persisting the turn."""
    from app.ai.vision import describe_image, vision_available
    from app.documents.storage import get_storage
    from app.graph.nodes.persist import persist_node

    if not vision_available():
        answer = (
            "Image understanding isn't configured yet. Add GEMINI_API_KEY (free tier) "
            "or set VISION_MODEL to a local Ollama vision model (e.g. `ollama pull "
            "llava`) and restart."
        )
    else:
        try:
            token = attachments[0]
            data = await get_storage().load(f"attachments/{user_id}/{token}")
            mime = "image/png" if token.endswith(".png") else "image/jpeg"
            answer = await describe_image(user_msg, data, mime)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.error("vision.failed", error=str(exc))
            answer = "I couldn't analyze that image right now — please try again."
    out: AssistantState = {
        "session_id": str(session_id),
        "user_msg": f"{user_msg} [image attached]",
        "final_text": answer,
        "route": "general_chat",
    }
    persisted = await persist_node(out)
    return {
        "type": "response",
        "message_id": persisted.get("message_id", ""),
        "message": answer,
        "route": "general_chat",
        "route_reason": "image understanding",
        "citations": [],
        "tool_calls": [],
        "actions": [],
        "data_as_of": datetime.now(UTC).isoformat(),
        "sources": [],
        "chart": None,
        "session_id": str(session_id),
    }


async def run_turn(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    user_name: str,
    user_msg: str,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Execute one turn through the graph; returns the WS response payload."""
    cid = new_correlation_id()
    if attachments:
        return await _vision_turn(session_id, user_id, user_msg, attachments)
    graph, has_checkpointer = await get_graph()
    user_instructions = await _load_instructions(user_id)
    from app.memory.facts import extract_and_store, recall

    memories = await recall(str(user_id), user_msg)
    if memories:
        memory_note = "Known about this user: " + "; ".join(memories)
        user_instructions = f"{user_instructions}\n{memory_note}".strip()
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
        "chart": {},
        "user_instructions": user_instructions,
    }
    if not has_checkpointer:
        turn_input["messages"] = await _load_history(session_id)
    config = {"configurable": {"thread_id": str(session_id)}}
    out: AssistantState = await graph.ainvoke(turn_input, config=config)
    log.info("turn.done", session_id=str(session_id), route=out.get("route"))
    _memory_task = asyncio.create_task(  # noqa: RUF006 — fire-and-forget
        extract_and_store(str(user_id), user_msg, out.get("final_text", ""))
    )
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
        "chart": out.get("chart") or None,
        "session_id": str(session_id),
    }
