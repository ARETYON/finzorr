"""LangSmith bootstrap: exports exactly the right env vars when enabled,
touches NOTHING when disabled (the default), and never requires a network.
"""

import os

import pytest

from app.core.config import settings
from app.core.langsmith_setup import setup_langsmith

pytestmark = pytest.mark.sanity

_VARS = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)


def test_disabled_by_default_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "")
    assert setup_langsmith() is False
    assert all(var not in os.environ for var in _VARS)


def test_enabled_without_key_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "")
    assert setup_langsmith() is False
    assert "LANGSMITH_TRACING" not in os.environ


def test_enabled_exports_and_clears_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "LANGSMITH_PROJECT", "finzorr-test")
    monkeypatch.setattr(settings, "LANGSMITH_ENDPOINT", "")
    assert setup_langsmith() is True
    assert os.environ["LANGSMITH_TRACING"] == "true"  # must be exactly "true"
    assert os.environ["LANGSMITH_API_KEY"] == "test-key-not-real"
    assert os.environ["LANGSMITH_PROJECT"] == "finzorr-test"
    assert "LANGSMITH_ENDPOINT" not in os.environ  # empty setting not exported

    # the latched lru_caches must now see tracing as enabled
    import langsmith.utils as ls_utils

    assert ls_utils.tracing_is_enabled() is True
    # leave the process clean for other tests: monkeypatch restores env,
    # but the caches latched True — clear them back
    from typing import Any, cast

    cast(Any, ls_utils.get_env_var).cache_clear()
    cast(Any, ls_utils.get_tracer_project).cache_clear()
