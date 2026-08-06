"""Deterministic WS-handler tests: auth rejection, origin policy, ping/pong,
malformed and unknown frames, unknown-session and empty-message errors — the
paths a browser E2E can't pin down and CI previously never saw (chat_ws sat
at 21% coverage).

The handler's two DB touchpoints (cookie auth, session ownership) are
stubbed so the frame protocol runs with no event-loop crossover between
starlette's TestClient portal and the async conftest fixtures; everything
else — parsing, dispatch, guards, close codes — is the real handler.
"""

import asyncio
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


# ------------------------------------------------------------ turn lifecycle
#
# The full streamed-turn path with a scripted run_turn: frames mirrored to
# the socket, the per-connection busy guard, and cancel -> stopped with the
# partial persisted. Locks/rate-limit are stubbed (Redis or the module-level
# _local set would leak state across tests) and every test uses a FRESH
# session id via the any-UUID ownership stub.


@pytest.fixture
def _stub_turn_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_user(_ws: Any) -> Any:
        return _FakeUser()

    async def any_owned(_user: Any, raw: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(raw)
        except (ValueError, TypeError):
            return None

    async def rate_ok(_uid: str) -> bool:
        return True

    async def claim_ok(_sid: str) -> bool:
        return True

    async def release_ok(_sid: str) -> None:
        return None

    monkeypatch.setattr(ws_mod, "get_ws_user", fake_user)
    monkeypatch.setattr(ws_mod, "_owned_session_id", any_owned)
    monkeypatch.setattr(ws_mod, "check_rate_limit", rate_ok)
    monkeypatch.setattr(ws_mod, "claim_turn", claim_ok)
    monkeypatch.setattr(ws_mod, "release_turn", release_ok)

    async def no_oob(*_a: Any, **_k: Any) -> None:
        return None

    # the generic-exception path persists partials via this — stub it so a
    # bug in a scripted fake can never reach live Postgres/Redis from tests
    monkeypatch.setattr(ws_mod, "record_out_of_band_turn", no_oob)


def test_ws_turn_mirrors_frames_then_response(
    _stub_turn_infra: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scripted_turn(
        session_id: uuid.UUID, *_a: Any, on_frame: Any = None, **_k: Any
    ) -> dict[str, Any]:
        await on_frame({"type": "routing", "route": "general_chat", "step": 1, "of": 1})
        await on_frame({"type": "token", "delta": "Hel"})
        await on_frame({"type": "token", "delta": "lo"})
        return {"type": "response", "message": "Hello", "session_id": str(session_id)}

    monkeypatch.setattr(ws_mod, "run_turn", scripted_turn)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "chat", "session_id": str(uuid.uuid4()), "message": "hi"})
        frames = [ws.receive_json() for _ in range(5)]
    assert [f["type"] for f in frames] == ["thinking", "routing", "token", "token", "response"]
    assert frames[-1]["message"] == "Hello"


def test_ws_busy_guard_then_cancel_persists_partial(
    _stub_turn_infra: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted: list[tuple[str, str]] = []

    async def blocking_turn(
        _session_id: uuid.UUID, *_a: Any, on_frame: Any = None, **_k: Any
    ) -> dict[str, Any]:
        await on_frame({"type": "token", "delta": "part"})
        await on_frame({"type": "token", "delta": "ial"})
        # created INSIDE the fake, on the app's loop — held open until the
        # cancel path kills the task (never signalled cross-thread)
        await asyncio.Event().wait()
        return {"type": "response"}  # pragma: no cover — always cancelled

    async def record_partial(
        _session_id: uuid.UUID, _user_msg: str, partial: str, turn_id: str = ""
    ) -> None:
        persisted.append((partial, turn_id))

    monkeypatch.setattr(ws_mod, "run_turn", blocking_turn)
    monkeypatch.setattr(ws_mod, "_persist_partial", record_partial)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "chat", "session_id": str(uuid.uuid4()), "message": "go"})
        assert ws.receive_json()["type"] == "thinking"
        assert ws.receive_json()["delta"] == "part"
        assert ws.receive_json()["delta"] == "ial"

        # per-CONNECTION busy guard (the per-session claim says "...in this chat")
        ws.send_json({"type": "chat", "session_id": str(uuid.uuid4()), "message": "again"})
        assert ws.receive_json() == {
            "type": "error",
            "message": "a reply is already in progress",
        }

        # cancel BEFORE leaving the context — tearing down with a blocked
        # turn in flight would race the shielded cleanup
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "stopped"}

    assert persisted, "cancel must persist the mirrored partial"
    partial_text, turn_id = persisted[0]
    assert partial_text == "partial"  # the joined token deltas the user saw
    assert turn_id  # shared id keeps the cancel path idempotent
