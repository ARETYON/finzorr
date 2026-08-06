"""Per-turn request context so tool handlers can act for the current user."""

from contextvars import ContextVar

_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")


def set_current_user(user_id: str) -> None:
    _current_user_id.set(user_id)


def get_current_user_id() -> str:
    return _current_user_id.get()
