"""Job-search tool via the Adzuna API (GATED-ON-KEYS, India-scoped).

Registers only when both ADZUNA_APP_ID and ADZUNA_APP_KEY are configured
(free tier ~1000 calls/month — results are Redis-cached to protect it).
Listings are external text, so output goes through the untrusted fence.
"""

from typing import Any

import httpx

from app.core.config import settings
from app.core.untrusted import wrap_untrusted
from app.infrastructure.llm.base import ToolDefinition
from app.market_data.cache import cached_json
from app.tools_registry.dispatcher import register_tool

_API_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"
_TIMEOUT_S = 15.0
_CACHE_TTL_S = 3600
_MAX_RESULTS = 10


async def _search_jobs(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' is required."
    location = str(args.get("location", "")).strip()
    raw_max = args.get("max_results", 8)
    limit = min(int(raw_max) if isinstance(raw_max, int) else 8, _MAX_RESULTS)
    try:
        listings = await cached_json(
            f"jobs:in:{query.lower()}:{location.lower()}:{limit}",
            _CACHE_TTL_S,
            lambda: _fetch(query, location, limit),
        )
    except Exception as exc:  # noqa: BLE001 — the never-raise contract
        return f"Error: job search failed — {type(exc).__name__}."
    if not listings:
        return f"No job listings found for '{query}'" + (f" in {location}" if location else "")
    lines = [
        f"- {job['title']} | {job['company']} | {job['location']} | "
        f"{job['salary']} | {job['created']} | {job['url']}"
        for job in listings
    ]
    return wrap_untrusted("\n".join(lines), "job listings")


async def _fetch(query: str, location: str, limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "app_id": settings.ADZUNA_APP_ID,
        "app_key": settings.ADZUNA_APP_KEY,
        "what": query,
        "results_per_page": limit,
        "content-type": "application/json",
    }
    if location:
        params["where"] = location
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.get(_API_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    listings: list[dict[str, Any]] = []
    for item in payload.get("results", []):
        salary_min, salary_max = item.get("salary_min"), item.get("salary_max")
        salary = (
            f"₹{salary_min:,.0f}–₹{salary_max:,.0f}"
            if salary_min and salary_max
            else "salary undisclosed"
        )
        listings.append(
            {
                "title": str(item.get("title", "")),
                "company": str(item.get("company", {}).get("display_name", "unknown")),
                "location": str(item.get("location", {}).get("display_name", "India")),
                "salary": salary,
                "created": str(item.get("created", ""))[:10],
                "url": str(item.get("redirect_url", "")),
            }
        )
    return listings


def register_jobs_tools() -> int:
    """Register the Adzuna job-search tool only when both keys are configured."""
    if not (settings.ADZUNA_APP_ID and settings.ADZUNA_APP_KEY):
        return 0
    register_tool(
        ToolDefinition(
            name="search_jobs",
            description=(
                "Search current job openings in India (via Adzuna) by role/skill "
                "keywords, optionally filtered by city. Returns title, company, "
                "location, salary range and a link per listing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Role or skill keywords, e.g. 'python developer'",
                    },
                    "location": {
                        "type": "string",
                        "description": "City filter, e.g. 'Bangalore' (optional)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Listings to return, max 10 (default 8)",
                    },
                },
                "required": ["query"],
            },
        ),
        _search_jobs,
    )
    return 1
