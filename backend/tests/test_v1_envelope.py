"""X8 regressions: the /api/v1 cursor envelope on the two hot lists.

v1 must return {items, next_cursor, total} with stable keyset traversal
(no overlap, no gap); the /api alias must keep the bare-list shape; a
malformed cursor is a 422 with the stable error envelope, not a 500.
"""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.db.session import SessionLocal
from app.models.message import Message
from app.models.user import utcnow

pytestmark = pytest.mark.integration


async def _create_session(client: AsyncClient) -> str:
    response = await client.post("/api/chat/sessions", json={})
    assert response.status_code == 201
    return str(response.json()["id"])


async def _seed_messages(session_id: str, n: int) -> None:
    """Insert n messages with strictly increasing created_at."""
    base = utcnow()
    async with SessionLocal() as db:
        for i in range(n):
            db.add(
                Message(
                    session_id=uuid.UUID(session_id),
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"msg-{i}",
                    created_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()


async def test_sessions_v1_envelope_and_traversal(user_client: AsyncClient) -> None:
    ids = [await _create_session(user_client) for _ in range(3)]

    first = await user_client.get("/api/v1/chat/sessions?limit=2")
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"items", "next_cursor", "total"}
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    second = await user_client.get(
        "/api/v1/chat/sessions", params={"limit": 2, "cursor": body["next_cursor"]}
    )
    assert second.status_code == 200
    tail = second.json()
    assert len(tail["items"]) == 1
    assert tail["next_cursor"] is None
    seen = [item["id"] for item in body["items"] + tail["items"]]
    assert sorted(seen) == sorted(ids)  # no overlap, no gap

    # the /api alias keeps the bare-list shape
    legacy = await user_client.get("/api/chat/sessions")
    assert isinstance(legacy.json(), list)


async def test_messages_v1_envelope_pages_backward(user_client: AsyncClient) -> None:
    session_id = await _create_session(user_client)
    await _seed_messages(session_id, 5)

    first = await user_client.get(
        f"/api/v1/chat/sessions/{session_id}/messages", params={"limit": 3}
    )
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 5
    # newest window, ascending for direct rendering
    assert [m["content"] for m in body["items"]] == ["msg-2", "msg-3", "msg-4"]
    assert body["next_cursor"] is not None

    older = await user_client.get(
        f"/api/v1/chat/sessions/{session_id}/messages",
        params={"limit": 3, "cursor": body["next_cursor"]},
    )
    tail = older.json()
    assert [m["content"] for m in tail["items"]] == ["msg-0", "msg-1"]
    assert tail["next_cursor"] is None

    # the /api alias keeps the bare-list shape
    legacy = await user_client.get(f"/api/chat/sessions/{session_id}/messages")
    assert isinstance(legacy.json(), list)


async def test_v1_invalid_cursor_is_422_with_code(user_client: AsyncClient) -> None:
    response = await user_client.get(
        "/api/v1/chat/sessions", params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "request_id" in body
