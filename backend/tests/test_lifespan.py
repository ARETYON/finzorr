"""Integration: boot the REAL lifespan (startup + shutdown).

ASGITransport skips lifespan events, so without this a regression in tool
registration, tracing setup, or shutdown cleanup would pass CI green."""

import pytest

pytestmark = pytest.mark.integration


async def test_lifespan_starts_and_stops_cleanly(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import scheduler as scheduler_mod
    from app.main import app, lifespan

    # The REAL scheduler task still starts and is cancelled on shutdown —
    # but its first tick fires immediately, and a mid-query cancel can leave
    # a half-dead asyncpg connection in the shared pool that poisons a LATER
    # test's event loop (order-dependent flake found under pytest-randomly).
    # Stub the tick bodies; the lifecycle under test is unchanged.
    async def no_tick(_now: object) -> None:
        return None

    monkeypatch.setattr(scheduler_mod, "_run_briefings", no_tick)
    monkeypatch.setattr(scheduler_mod, "_run_alerts", no_tick)
    monkeypatch.setattr(scheduler_mod, "_run_tasks", no_tick)

    async with lifespan(app):
        # startup ran: the always-on tool families must be registered
        from app.tools_registry.dispatcher import all_tools

        names = {t.name for t in all_tools()}
        assert {"get_quote", "search_symbol", "read_url", "analyze_portfolio"} <= names
    # shutdown ran without raising (scheduler cancel + graph pool close)
