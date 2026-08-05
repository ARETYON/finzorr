"""Our own session JWT, carried in an httpOnly cookie.

PyJWT HS256 with SESSION_SECRET; 7-day expiry by default. Deliberately minimal:
sign and verify our own cookie — no OAuth client machinery.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

SESSION_COOKIE = "finzorr_session"
_ALGORITHM = "HS256"


def create_session_jwt(user_id: uuid.UUID) -> str:
    """Sign a session token for a user."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=settings.SESSION_TTL_DAYS),
    }
    return jwt.encode(payload, settings.SESSION_SECRET, algorithm=_ALGORITHM)


def verify_session_jwt(token: str) -> uuid.UUID | None:
    """Return the user id for a valid token, else None (never raises)."""
    try:
        payload = jwt.decode(token, settings.SESSION_SECRET, algorithms=[_ALGORITHM])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def cookie_kwargs() -> dict[str, object]:
    """Per-environment Set-Cookie attributes."""
    kwargs: dict[str, object] = {
        "httponly": True,
        "samesite": "lax",
        "secure": not settings.is_dev,
        "max_age": settings.SESSION_TTL_DAYS * 86400,
    }
    if settings.COOKIE_DOMAIN:
        kwargs["domain"] = settings.COOKIE_DOMAIN
    return kwargs
