"""LangSmith tracing bootstrap (opt-in; default OFF).

langsmith/langchain-core read configuration from environment variables, and
pydantic-settings does NOT export .env values to os.environ — so the enabled
settings are exported here, once, at startup. Both `get_env_var` and
`get_tracer_project` in langsmith are lru_cached: if anything probed tracing
before this ran, the False result is latched — the cache_clear calls make
startup ordering irrelevant.
"""

import os
from typing import Any, cast

from app.core.config import settings
from app.core.logging import log


def setup_langsmith() -> bool:
    """Export LangSmith env vars from Settings; True if tracing is live."""
    if not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY):
        return False
    os.environ["LANGSMITH_TRACING"] = "true"  # must be exactly "true"
    os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
    if settings.LANGSMITH_ENDPOINT:
        os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
    try:
        import langsmith.utils as ls_utils

        # typed as @overload so mypy can't see the lru_cache attribute
        cast(Any, ls_utils.get_env_var).cache_clear()
        cast(Any, ls_utils.get_tracer_project).cache_clear()
    except Exception as exc:  # noqa: BLE001 — tracing must never block startup
        log.warning("observability.langsmith_cache_clear_failed", error=str(exc))
    log.info("observability.langsmith", project=settings.LANGSMITH_PROJECT)
    return True
