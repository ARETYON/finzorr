"""Indian financial news via free publisher RSS feeds (keyless, always-on).

Feeds are fetched concurrently with per-feed isolation — one dead feed never
kills the tool. Headlines are external text, so output goes through the
untrusted fence. Verified live (freshness + shape) at build time; Moneycontrol
was dropped because every feed it serves stopped updating in 2024.
"""

import asyncio
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.logging import log
from app.core.untrusted import wrap_untrusted
from app.infrastructure.llm.base import ToolDefinition
from app.market_data.cache import cached_json
from app.tools_registry.dispatcher import register_tool

_FEEDS: tuple[tuple[str, str], ...] = (
    ("ET Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Livemint", "https://www.livemint.com/rss/markets"),
)
_TIMEOUT_S = 8.0
_CACHE_TTL_S = 600
_MAX_ITEMS = 15
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _parse_feed(xml: str, source: str) -> list[dict[str, str]]:
    """Extract item title/link/date from one RSS document (pure, offline-testable)."""
    soup = BeautifulSoup(xml, "xml")
    items = []
    for item in soup.find_all("item")[:_MAX_ITEMS]:
        title = item.find("title")
        link = item.find("link")
        pub_date = item.find("pubDate")
        if title is None or not title.get_text(strip=True):
            continue
        items.append(
            {
                "source": source,
                "title": title.get_text(strip=True),
                "link": link.get_text(strip=True) if link else "",
                "date": pub_date.get_text(strip=True) if pub_date else "",
            }
        )
    return items


async def _fetch_feed(source: str, url: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_S, headers={"User-Agent": _UA}, follow_redirects=True
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    return await asyncio.to_thread(_parse_feed, response.text, source)


def _sort_key(item: dict[str, str]) -> float:
    try:
        return -parsedate_to_datetime(item["date"]).timestamp()
    except (ValueError, TypeError):
        return 0.0


async def _fetch_all() -> list[dict[str, str]]:
    gathered = await asyncio.gather(
        *(_fetch_feed(source, url) for source, url in _FEEDS), return_exceptions=True
    )
    merged: list[dict[str, str]] = []
    for (source, _), result in zip(_FEEDS, gathered, strict=True):
        if isinstance(result, BaseException):
            log.warning("news.feed_failed", source=source, error=type(result).__name__)
            continue
        merged.extend(result)
    seen: set[str] = set()
    deduped = []
    for item in sorted(merged, key=_sort_key):
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        deduped.append(item)
    return deduped


async def _get_market_news(args: dict[str, Any]) -> str:
    topic = str(args.get("topic", "")).strip().lower()
    raw_limit = args.get("limit", 8)
    limit = min(int(raw_limit) if isinstance(raw_limit, int) else 8, _MAX_ITEMS)
    try:
        items = await cached_json(f"news:{topic}", _CACHE_TTL_S, _filtered_fetch(topic))
    except Exception as exc:  # noqa: BLE001 — the never-raise contract
        return f"Error: news fetch failed — {type(exc).__name__}."
    if not items:
        return "Error: no news feeds are reachable right now."
    lines = [
        f"- [{item['source']}] {item['title']} ({item['date']}) {item['link']}"
        for item in items[:limit]
    ]
    return wrap_untrusted("\n".join(lines), "news headlines")


def _filtered_fetch(topic: str) -> Any:
    async def fetch() -> list[dict[str, str]]:
        items = await _fetch_all()
        if topic:
            items = [i for i in items if topic in i["title"].lower()]
        return items

    return fetch


register_tool(
    ToolDefinition(
        name="get_market_news",
        description=(
            "Latest Indian stock-market news headlines from ET Markets, Business "
            "Standard and Livemint RSS feeds, optionally filtered by a topic "
            "keyword (e.g. 'nifty', 'RBI', a company name)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Keyword filter on headlines (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Headlines to return, max 15 (default 8)",
                },
            },
        },
    ),
    _get_market_news,
)
