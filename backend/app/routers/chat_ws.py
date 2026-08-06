"""WebSocket chat endpoint.

Frame protocol:
  client -> server: {"type":"chat","message":...,"session_id":...} | {"type":"ping"}
                  | {"type":"cancel"}
  server -> client: {"type":"thinking"} | {"type":"routing",...} | {"type":"token",...}
                  | {"type":"tool_call",...} | {"type":"response",...}
                  | {"type":"stopped"} | {"type":"error",...} | {"type":"pong"}

The turn runs as a background task so the receive loop stays free — that is
what makes mid-stream `cancel` actually work.

Security: WebSockets ignore CORS, so the Origin header is validated manually
before accept(); the session cookie is verified at handshake (close 4401).
"""

import asyncio
import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.dependencies import get_ws_user
from app.core.config import settings
from app.core.logging import log
from app.core.rate_limit import check_rate_limit
from app.core.turn_lock import claim as claim_turn
from app.core.turn_lock import release as release_turn
from app.db.session import SessionLocal
from app.graph.turn import record_out_of_band_turn, resume_turn, run_turn
from app.models.chat_session import ChatSession
from app.models.user import User

router = APIRouter(tags=["chat-ws"])

WS_POLICY_VIOLATION = 1008
WS_UNAUTHORIZED = 4401

# One in-flight turn per SESSION, across sockets AND workers: the
# per-connection `busy` guard can't stop two tabs (or two uvicorn workers)
# running concurrent graph invocations on one thread_id. The Redis-backed
# lock in core/turn_lock handles both, with a TTL self-heal.


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin", "")
    return origin == settings.FRONTEND_ORIGIN or (settings.is_dev and not origin)


async def _persist_partial(
    session_id: uuid.UUID, user_msg: str, partial: str, turn_id: str = ""
) -> None:
    """Persist a cancelled turn (DB + checkpointer) so UI and model agree."""
    text = f"{partial}\n\n_(stopped by user)_" if partial.strip() else "_(stopped by user)_"
    try:
        await record_out_of_band_turn(session_id, user_msg, text, turn_id=turn_id)
    except Exception as exc:  # noqa: BLE001 — cancellation cleanup must not raise
        log.warning("ws.cancel.persist_failed", error=str(exc))


def _parse_attachments(data: dict[str, Any]) -> list[str]:
    """Tolerate any client-supplied shape: null, non-list, mixed types."""
    raw = data.get("attachments")
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, str)][:1]


async def _owned_session_id(user: User, raw: str) -> uuid.UUID | None:
    try:
        session_id = uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None or session.user_id != user.id:
            return None
    return session_id


class _Connection:
    """Per-socket state: at most one in-flight turn at a time."""

    def __init__(self, websocket: WebSocket, user: User) -> None:
        self.ws = websocket
        self.user = user
        self.turn_task: asyncio.Task[None] | None = None

    @property
    def busy(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()

    async def send(self, frame: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):  # socket may be gone mid-send
            await self.ws.send_json(frame)

    async def start_turn(self, data: dict[str, Any]) -> None:
        if self.busy:
            await self.send({"type": "error", "message": "a reply is already in progress"})
            return
        session_id = await _owned_session_id(self.user, str(data.get("session_id", "")))
        if session_id is None:
            await self.send({"type": "error", "message": "unknown session"})
            return
        if not await check_rate_limit(str(self.user.id)):
            await self.send(
                {"type": "error", "message": "Rate limit reached — please wait a few minutes."}
            )
            return
        user_msg = str(data.get("message", "")).strip()
        if not user_msg:
            await self.send({"type": "error", "message": "empty message"})
            return
        # ALL client-data parsing happens BEFORE the session is claimed — a
        # malformed frame raising after add() would brick the session for the
        # process lifetime (only _run releases the guard).
        attachments = _parse_attachments(data)
        sid = str(session_id)
        if not await claim_turn(sid):
            await self.send(
                {"type": "error", "message": "a reply is already in progress in this chat"}
            )
            return
        try:
            await self.send({"type": "thinking"})
            self.turn_task = asyncio.create_task(self._run(session_id, user_msg, attachments))
        except BaseException:
            await release_turn(sid)  # claim must never outlive a failed start
            raise

    async def _run(
        self, session_id: uuid.UUID, user_msg: str, attachments: list[str]
    ) -> None:
        # Mirror streamed tokens server-side so a cancelled turn can persist
        # what the user actually saw — cancellation unwinds past persist_node,
        # which otherwise loses both sides of the turn on refresh.
        partial: list[str] = []
        turn_id = uuid.uuid4().hex  # shared with the cancel path (idempotency)

        async def _send_and_record(frame: dict[str, Any]) -> None:
            if frame.get("type") == "token":
                partial.append(str(frame.get("delta", "")))
            await self.send(frame)

        # The lock is released BEFORE each terminal frame — a client that
        # reacts instantly to `response` must not bounce off its own turn's
        # not-yet-released claim. The finally is the idempotent backstop.
        try:
            response = await run_turn(
                session_id,
                self.user.id,
                self.user.name,
                user_msg,
                attachments or None,
                on_frame=_send_and_record,
                turn_id=turn_id,
            )
            await release_turn(str(session_id))
            await self.send(response)
        except asyncio.CancelledError:
            await asyncio.shield(
                _persist_partial(session_id, user_msg, "".join(partial), turn_id)
            )
            await asyncio.shield(release_turn(str(session_id)))
            await self.send({"type": "stopped"})
        except Exception as exc:  # noqa: BLE001 — degrade, keep the socket alive
            log.error("ws.turn.error", error=str(exc))
            await release_turn(str(session_id))
            await self.send({"type": "error", "message": "assistant error — please retry"})
        finally:
            await asyncio.shield(release_turn(str(session_id)))

    async def start_approval(self, data: dict[str, Any]) -> None:
        """Resume a turn parked at a HITL interrupt with approve/decline."""
        if self.busy:
            await self.send({"type": "error", "message": "a reply is already in progress"})
            return
        session_id = await _owned_session_id(self.user, str(data.get("session_id", "")))
        if session_id is None:
            await self.send({"type": "error", "message": "unknown session"})
            return
        approved = bool(data.get("approved", False))
        sid = str(session_id)
        if not await claim_turn(sid):
            await self.send(
                {"type": "error", "message": "a reply is already in progress in this chat"}
            )
            return
        try:
            await self.send({"type": "thinking"})
            self.turn_task = asyncio.create_task(self._run_resume(session_id, approved))
        except BaseException:
            await release_turn(sid)
            raise

    async def _run_resume(self, session_id: uuid.UUID, approved: bool) -> None:
        try:
            response = await resume_turn(session_id, approved, on_frame=self.send)
            await release_turn(str(session_id))
            await self.send(response)
        except asyncio.CancelledError:
            await asyncio.shield(release_turn(str(session_id)))
            await self.send({"type": "stopped"})
        except Exception as exc:  # noqa: BLE001 — degrade, keep the socket alive
            log.error("ws.resume.error", error=str(exc))
            await release_turn(str(session_id))
            await self.send({"type": "error", "message": "assistant error — please retry"})
        finally:
            await asyncio.shield(release_turn(str(session_id)))

    def cancel_turn(self) -> bool:
        if self.busy and self.turn_task is not None:
            self.turn_task.cancel()
            return True
        return False


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket):
        await websocket.close(code=WS_POLICY_VIOLATION)
        return
    user = await get_ws_user(websocket)
    if user is None:
        await websocket.close(code=WS_UNAUTHORIZED)
        return
    await websocket.accept()
    conn = _Connection(websocket, user)
    log.info("ws.connected", user_id=str(user.id))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await conn.send({"type": "error", "message": "invalid frame"})
                continue
            if not isinstance(data, dict):  # "[]" / "42" would kill the handler
                await conn.send({"type": "error", "message": "invalid frame"})
                continue
            frame_type = data.get("type")
            if frame_type == "ping":
                await conn.send({"type": "pong"})
            elif frame_type == "cancel":
                if not conn.cancel_turn():
                    await conn.send({"type": "stopped"})
            elif frame_type == "chat":
                await conn.start_turn(data)
            elif frame_type == "approval":
                await conn.start_approval(data)
            else:
                await conn.send({"type": "error", "message": "unknown frame type"})
    except WebSocketDisconnect:
        conn.cancel_turn()
        log.info("ws.disconnected", user_id=str(user.id))
