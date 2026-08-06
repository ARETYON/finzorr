"""Web search chain: Tavily (if key) -> SearXNG (if URL) -> DuckDuckGo (keyless).

Sequential with silent per-provider exception swallowing — the first provider
returning results wins; "none" means every provider failed.
"""

import asyncio
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from langsmith import traceable

from app.core.config import settings
from app.core.logging import log

_TIMEOUT_S = 12.0
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SNIPPET_CHARS = 400


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


async def _tavily(query: str, max_results: int) -> list[SearchResult]:
    if not settings.TAVILY_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.TAVILY_API_KEY, "query": query, "max_results": max_results},
        )
        response.raise_for_status()
        return [
            SearchResult(r["title"], r["url"], str(r.get("content", ""))[:SNIPPET_CHARS])
            for r in response.json().get("results", [])
        ]


async def _searxng(query: str, max_results: int) -> list[SearchResult]:
    if not settings.SEARXNG_URL:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers={"User-Agent": _UA}) as client:
        response = await client.get(
            f"{settings.SEARXNG_URL}/search", params={"q": query, "format": "json"}
        )
        response.raise_for_status()
        return [
            SearchResult(r["title"], r["url"], str(r.get("content", ""))[:SNIPPET_CHARS])
            for r in response.json().get("results", [])[:max_results]
        ]


async def _duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_S, headers={"User-Agent": _UA}, follow_redirects=True
    ) as client:
        response = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        response.raise_for_status()
    # SERP parsing is CPU-bound lxml work — keep it off the event loop.
    return await asyncio.to_thread(_parse_duckduckgo, response.text, max_results)


def _parse_duckduckgo(html: str, max_results: int) -> list[SearchResult]:
    soup = BeautifulSoup(html, "lxml")
    results: list[SearchResult] = []
    for block in soup.select("div.result")[:max_results]:
        link = block.select_one("a.result__a")
        snippet = block.select_one(".result__snippet")
        if link and link.get("href"):
            results.append(
                SearchResult(
                    title=link.get_text(strip=True),
                    url=str(link["href"]),
                    snippet=snippet.get_text(strip=True)[:SNIPPET_CHARS] if snippet else "",
                )
            )
    return results


@traceable(run_type="retriever", name="web_search")
async def search(query: str, max_results: int = 6) -> tuple[list[SearchResult], str]:
    """Try each provider in order; returns (results, provider_name|'none')."""
    for name, fn in (("tavily", _tavily), ("searxng", _searxng), ("duckduckgo", _duckduckgo)):
        try:
            results = await fn(query, max_results)
            if results:
                return results, name
        except Exception as exc:  # noqa: BLE001 — try the next provider
            log.warning("web_search.provider_failed", provider=name, error=type(exc).__name__)
    return [], "none"
