"""Integration: boot the REAL lifespan (startup + shutdown).

ASGITransport skips lifespan events, so without this a regression in tool
registration, tracing setup, or shutdown cleanup would pass CI green."""

import pytest

pytestmark = pytest.mark.integration


async def test_lifespan_starts_and_stops_cleanly(_database: None) -> None:
    from app.main import app, lifespan

    async with lifespan(app):
        # startup ran: the always-on tool families must be registered
        from app.tools_registry.dispatcher import all_tools

        names = {t.name for t in all_tools()}
        assert {"get_quote", "search_symbol", "read_url", "deep_research"} <= names
    # shutdown ran without raising (scheduler cancel + graph pool close)
