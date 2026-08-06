"""Structured logging with per-turn correlation IDs.

structlog renders JSON lines; `new_correlation_id()` binds a short ID into the
async context so every log call in one request/WS-turn is grep-able end to end
(gateway -> supervisor -> node -> tool -> persist).
"""

import logging
import uuid
from contextvars import ContextVar

import structlog

from app.core.config import settings

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def configure_logging() -> None:
    """Configure structlog once at startup; safe to call repeatedly."""
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def new_correlation_id(inherit: str | None = None) -> str:
    """Bind a correlation ID to the logging context.

    `inherit` adopts an upstream id (proxy/LB X-Request-ID propagation) so
    one request keeps one id across hops; sanitized and length-capped since
    it arrives from a header.
    """
    cid = "".join(c for c in inherit if c.isalnum() or c == "-")[:64] if inherit else ""
    cid = cid or uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    structlog.contextvars.bind_contextvars(correlation_id=cid)
    return cid


def get_correlation_id() -> str:
    """Return the correlation ID bound to the current async context."""
    return _correlation_id.get()


log = structlog.get_logger()
