"""Integration: an out-of-band (cancelled/timed-out) turn must land in BOTH
stores — the DB the UI reloads and the checkpointer thread the model reads.
Divergence here was the re-review's top new finding."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.integration


async def test_out_of_band_turn_updates_both_stores(user_client: AsyncClient) -> None:
    from app.infrastructure.db.session import SessionLocal
    from app.models.message import Message
    from app.orchestration.graph import close_graph, get_graph
    from app.orchestration.turn import record_out_of_band_turn

    created = await user_client.post("/api/chat/sessions", json={})
    session_id = uuid.UUID(created.json()["id"])

    try:
        await record_out_of_band_turn(session_id, "essay please", "partial…\n\n_(stopped)_")

        # store 1: the DB (what a refresh shows)
        async with SessionLocal() as db:
            result = await db.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
            )
            rows = list(result.scalars())
        assert [r.role for r in rows] == ["user", "assistant"]
        assert "stopped" in rows[1].content

        # store 2: the checkpointer thread (what the model remembers)
        graph, has_checkpointer = await get_graph()
        assert has_checkpointer, "test DB must support the checkpointer"
        state = await graph.aget_state({"configurable": {"thread_id": str(session_id)}})
        messages = state.values.get("messages", [])
        assert {"role": "user", "content": "essay please"} in messages
        assert any(
            m.get("role") == "assistant" and "stopped" in m.get("content", "")
            for m in messages
        )
    finally:
        # the pool binds to this test's event loop — release it so later
        # tests (with fresh loops) never touch a dead pool
        await close_graph()
