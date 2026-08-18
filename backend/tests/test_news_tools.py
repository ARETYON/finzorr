"""Sanity: RSS market-news tool — parsing, merge/sort, failure isolation. No live network."""

from typing import Any

import pytest
import respx
from httpx import Response

import app.tools_registry.news_tools as news_tools
from app.tools_registry.dispatcher import all_tools

pytestmark = pytest.mark.sanity

_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item><title>Nifty hits record high</title>
<link>https://example.com/nifty</link>
<pubDate>Thu, 13 Aug 2026 10:00:00 +0530</pubDate></item>
<item><title>RBI holds repo rate</title>
<link>https://example.com/rbi</link>
<pubDate>Thu, 13 Aug 2026 09:00:00 +0530</pubDate></item>
<item><title></title><link>https://example.com/empty</link></item>
</channel></rss>"""


async def _passthrough_cache(key: str, ttl_s: int, fetch: Any) -> Any:
    return await fetch()


def test_parse_feed_extracts_items() -> None:
    items = news_tools._parse_feed(_RSS_FIXTURE, "Test Feed")
    assert len(items) == 2  # empty-title item dropped
    assert items[0]["title"] == "Nifty hits record high"
    assert items[0]["source"] == "Test Feed"
    assert items[0]["link"] == "https://example.com/nifty"


def test_news_tool_is_registered() -> None:
    assert "get_market_news" in {t.name for t in all_tools()}


@respx.mock
async def test_get_market_news_merges_and_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(news_tools, "cached_json", _passthrough_cache)
    for _, url in news_tools._FEEDS:
        respx.get(url).mock(return_value=Response(200, text=_RSS_FIXTURE))
    result = await news_tools._get_market_news({})
    assert "Nifty hits record high" in result
    assert "UNTRUSTED CONTENT" in result
    # dedupe: identical titles across feeds appear once
    assert result.count("Nifty hits record high") == 1


@respx.mock
async def test_get_market_news_topic_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(news_tools, "cached_json", _passthrough_cache)
    for _, url in news_tools._FEEDS:
        respx.get(url).mock(return_value=Response(200, text=_RSS_FIXTURE))
    result = await news_tools._get_market_news({"topic": "rbi"})
    assert "RBI holds repo rate" in result
    assert "Nifty hits record high" not in result


@respx.mock
async def test_one_dead_feed_does_not_kill_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_tools, "cached_json", _passthrough_cache)
    feeds = news_tools._FEEDS
    respx.get(feeds[0][1]).mock(return_value=Response(500))
    for _, url in feeds[1:]:
        respx.get(url).mock(return_value=Response(200, text=_RSS_FIXTURE))
    result = await news_tools._get_market_news({})
    assert "Nifty hits record high" in result


@respx.mock
async def test_all_feeds_dead_returns_error_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_tools, "cached_json", _passthrough_cache)
    for _, url in news_tools._FEEDS:
        respx.get(url).mock(return_value=Response(500))
    result = await news_tools._get_market_news({})
    assert result == "Error: no news feeds are reachable right now."
