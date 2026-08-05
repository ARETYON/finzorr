"""Per-session streaming callback registry.

WS callbacks are plain async callables and must never enter graph state —
the checkpointer would try (and fail) to serialize them. Single-process only,
matching the single-container deployment model.
"""

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

StreamCallback = Callable[[dict[str, Any]], Awaitable[None]]

_callbacks: dict[str, StreamCallback] = {}


def set_callback(session_id: str, cb: StreamCallback) -> None:
    _callbacks[session_id] = cb


def get_callback(session_id: str) -> StreamCallback | None:
    return _callbacks.get(session_id)


def clear_callback(session_id: str) -> None:
    _callbacks.pop(session_id, None)


async def emit(session_id: str, frame: dict[str, Any]) -> None:
    """Best-effort frame emission; a broken socket never crashes a turn."""
    cb = _callbacks.get(session_id)
    if cb is None:
        return
    with contextlib.suppress(Exception):
        await cb(frame)
