"""Deterministic WS-handler tests: auth rejection, origin policy, ping/pong,
malformed and unknown frames, unknown-session and empty-message errors — the
paths a browser E2E can't pin down and CI previously never saw (chat_ws sat
at 21% coverage).

The handler's two DB touchpoints (cookie auth, session ownership) are
stubbed so the frame protocol runs with no event-loop crossover between
starlette's TestClient portal and the async conftest fixtures; everything
else — parsing, dispatch, guards, close codes — is the real handler.
"""

import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.routers import chat_ws as ws_mod

pytestmark = pytest.mark.sanity

_USER_ID = uuid.uuid4()
_SESSION_ID = uuid.uuid4()


class _FakeUser:
    id = _USER_ID
    name = "Dev"


@pytest.fixture
def _stub_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_user(_ws: Any) -> Any:
        return _FakeUser()

    async def fake_owned(_user: Any, raw: str) -> uuid.UUID | None:
        try:
            session_id = uuid.UUID(raw)
        except (ValueError, TypeError):
            return None
        return session_id if session_id == _SESSION_ID else None

    monkeypatch.setattr(ws_mod, "get_ws_user", fake_user)
    monkeypatch.setattr(ws_mod, "_owned_session_id", fake_owned)


def test_ws_rejects_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_user(_ws: Any) -> None:
        return None

    monkeypatch.setattr(ws_mod, "get_ws_user", no_user)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/chat") as ws:
            ws.receive_json()
    assert excinfo.value.code == ws_mod.WS_UNAUTHORIZED


def test_ws_rejects_cross_origin(_stub_db: None) -> None:
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/ws/chat", headers={"Origin": "https://evil.example"}
        ) as ws:
            ws.receive_json()
    assert excinfo.value.code == ws_mod.WS_POLICY_VIOLATION


def test_ws_ping_pong_and_bad_frames(_stub_db: None) -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        ws.send_text("{not json")
        assert ws.receive_json()["message"] == "invalid frame"

        ws.send_text("[1, 2, 3]")  # valid JSON, not an object
        assert ws.receive_json()["message"] == "invalid frame"

        ws.send_json({"type": "warp"})
        assert ws.receive_json()["message"] == "unknown frame type"

        # cancel with nothing in flight answers stopped (idempotent)
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "stopped"}


def test_ws_chat_unknown_session_errors(_stub_db: None) -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        for bad in ("not-a-uuid", str(uuid.uuid4())):
            ws.send_json({"type": "chat", "session_id": bad, "message": "hi"})
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "unknown session" in frame["message"]


def test_ws_empty_message_errors(_stub_db: None) -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "chat", "session_id": str(_SESSION_ID), "message": "   "})
        assert ws.receive_json()["message"] == "empty message"
