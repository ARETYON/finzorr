"""Per-turn request context so tool handlers can act for the current user.

Always set/reset as a pair (`user_context` manager). A bare `.set()` with no
reset leaks: LangGraph's single-task fast path runs nodes in the caller's
context, and the scheduler runs many users' turns inside ONE long-lived task —
a stale user_id there is a cross-tenant identity leak.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")


@contextmanager
def user_context(user_id: str) -> Iterator[None]:
    """Bind the current user for the duration of a turn, then restore."""
    token = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)


def get_current_user_id() -> str:
    return _current_user_id.get()
