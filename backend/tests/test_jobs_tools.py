"""Sanity: Adzuna jobs tool — gating, happy path, error strings. No live network."""

from typing import Any

import pytest
import respx
from httpx import Response

import app.tools_registry.jobs_tools as jobs_tools
from app.core.config import settings
from app.tools_registry.dispatcher import all_tools

pytestmark = pytest.mark.sanity


async def _passthrough_cache(key: str, ttl_s: int, fetch: Any) -> Any:
    return await fetch()


def test_jobs_tools_absent_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ADZUNA_APP_ID", "")
    monkeypatch.setattr(settings, "ADZUNA_APP_KEY", "")
    assert jobs_tools.register_jobs_tools() == 0


def test_jobs_tools_absent_with_only_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ADZUNA_APP_ID", "id-only")
    monkeypatch.setattr(settings, "ADZUNA_APP_KEY", "")
    assert jobs_tools.register_jobs_tools() == 0


def test_jobs_tools_register_with_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(settings, "ADZUNA_APP_KEY", "app-key")
    assert jobs_tools.register_jobs_tools() == 1
    assert "search_jobs" in {t.name for t in all_tools()}


async def test_search_jobs_requires_query() -> None:
    assert await jobs_tools._search_jobs({}) == "Error: 'query' is required."


@respx.mock
async def test_search_jobs_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_tools, "cached_json", _passthrough_cache)
    respx.get(jobs_tools._API_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "title": "Python Developer",
                        "company": {"display_name": "Acme India"},
                        "location": {"display_name": "Bangalore"},
                        "salary_min": 1200000,
                        "salary_max": 1800000,
                        "created": "2026-08-01T00:00:00Z",
                        "redirect_url": "https://example.com/job/1",
                    }
                ]
            },
        )
    )
    result = await jobs_tools._search_jobs({"query": "python developer"})
    assert "Python Developer" in result
    assert "Acme India" in result
    assert "Bangalore" in result
    assert "UNTRUSTED CONTENT" in result  # fenced as data


@respx.mock
async def test_search_jobs_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_tools, "cached_json", _passthrough_cache)
    respx.get(jobs_tools._API_URL).mock(return_value=Response(200, json={"results": []}))
    result = await jobs_tools._search_jobs({"query": "unicorn wrangler", "location": "Pune"})
    assert result == "No job listings found for 'unicorn wrangler' in Pune"


@respx.mock
async def test_search_jobs_api_failure_is_error_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_tools, "cached_json", _passthrough_cache)
    respx.get(jobs_tools._API_URL).mock(return_value=Response(500))
    result = await jobs_tools._search_jobs({"query": "python"})
    assert result.startswith("Error: job search failed")
