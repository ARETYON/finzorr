"""Sanity: config-gated integrations — Google OAuth, vision, GitHub MCP,
fundamentals-refresh failure isolation. No network anywhere."""

import asyncio
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import InvalidToken

import app.mcp_client.github_client as github_client
from app.core.config import settings
from app.core.request_context import user_context
from app.infrastructure.llm.vision import _client_and_model, vision_available
from app.integrations.google_connect import (
    SCOPES,
    _fernet,
    _has_drive_scope,
    authorize_url,
    connectors_enabled,
    register_google_tools,
)
from app.market_data.base import CompanyOverview, QuoteData
from app.nl2sql.jobs.refresh_fundamentals import _fetch_one
from app.tools_registry.dispatcher import all_tools, dispatch

pytestmark = pytest.mark.sanity


# ---------------------------------------------------------------- google gating


def test_connectors_disabled_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
    assert connectors_enabled() is False


def test_connectors_disabled_without_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "sec")
    assert connectors_enabled() is False


def test_connectors_enabled_with_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "sec")
    assert connectors_enabled() is True


def test_register_google_tools_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
    assert register_google_tools() == 0


def test_register_google_tools_registers_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "sec")
    assert register_google_tools() == 4
    names = {t.name for t in all_tools()}
    assert {
        "gmail_search",
        "calendar_upcoming_events",
        "drive_search_files",
        "drive_read_file",
    } <= names


# ---------------------------------------------------------------- drive scope gating


def test_has_drive_scope_detects_legacy_and_new_tokens() -> None:
    legacy = (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/calendar.readonly"
    )
    assert _has_drive_scope(legacy) is False
    assert _has_drive_scope(legacy + " https://www.googleapis.com/auth/drive.readonly") is True


async def test_drive_search_asks_reconnect_on_legacy_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.integrations.google_connect as gc

    async def legacy_scopes(user_id: Any) -> str:
        return "https://www.googleapis.com/auth/gmail.readonly"

    monkeypatch.setattr(gc, "_user_scopes", legacy_scopes)
    with user_context(str(uuid.uuid4())):
        result = await gc._drive_search({"query": "budget"})
    assert result == gc._DRIVE_NOT_GRANTED


async def test_drive_read_requires_file_id() -> None:
    import app.integrations.google_connect as gc

    assert await gc._drive_read({}) == "Error: 'file_id' is required."


# ---------------------------------------------------------------- authorize_url


def test_authorize_url_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(settings, "OAUTH_REDIRECT_URL", "http://localhost:8000/cb")
    url = authorize_url("state-abc")
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "accounts.google.com"
    assert parts.path == "/o/oauth2/v2/auth"
    params = parse_qs(parts.query)
    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["http://localhost:8000/cb"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == [" ".join(SCOPES)]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["state"] == ["state-abc"]


def test_scopes_are_read_only() -> None:
    assert all(scope.endswith(".readonly") for scope in SCOPES)


# ---------------------------------------------------------------- fernet


def test_fernet_key_is_deterministic_per_secret() -> None:
    token = _fernet().encrypt(b"refresh-token")
    # a NEW instance derives the same key from SESSION_SECRET
    assert _fernet().decrypt(token) == b"refresh-token"


def test_fernet_key_changes_with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _fernet().encrypt(b"refresh-token")
    monkeypatch.setattr(settings, "SESSION_SECRET", "a-different-secret")
    with pytest.raises(InvalidToken):
        _fernet().decrypt(token)


# ---------------------------------------------------------------- vision gating


def test_vision_unavailable_without_any_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "VISION_MODEL", "")
    assert vision_available() is False


def test_vision_available_with_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(settings, "VISION_MODEL", "")
    assert vision_available() is True
    client, model = _client_and_model()
    assert model == settings.GEMINI_MODEL
    assert "generativelanguage.googleapis.com" in str(client.base_url)


def test_vision_available_with_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "VISION_MODEL", "llava")
    assert vision_available() is True
    client, model = _client_and_model()
    assert model == "llava"
    assert str(client.base_url).startswith(settings.OLLAMA_URL)


# ---------------------------------------------------------------- github MCP gating


class _FakeMCPClient:
    def __init__(self, name: str, url: str, headers: dict[str, str]) -> None:
        self.name = name
        self.url = url
        self.headers = headers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "get_me", "description": "who am I", "inputSchema": {"type": "object"}},
            {"name": "delete_repo", "description": "destructive", "inputSchema": {}},
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return "tool-ok"


class _BrokenMCPClient(_FakeMCPClient):
    async def initialize(self) -> None:
        raise RuntimeError("mcp unreachable")


async def test_github_tools_absent_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "")
    monkeypatch.setattr(github_client, "_client", None)
    assert await github_client.register_github_tools() == 0
    assert github_client._client is None  # never even constructed


async def test_github_unreachable_server_registers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(github_client, "_client", None)  # restored after the test
    monkeypatch.setattr(github_client, "MCPClient", _BrokenMCPClient)
    assert await github_client.register_github_tools() == 0


async def test_github_registers_only_allowlisted_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(github_client, "_client", None)  # restored after the test
    monkeypatch.setattr(github_client, "MCPClient", _FakeMCPClient)
    assert await github_client.register_github_tools() == 1
    names = {t.name for t in all_tools()}
    assert "github_get_me" in names
    assert "github_delete_repo" not in names  # write tool filtered out
    # the registered handler proxies through the MCP client
    assert await dispatch("github_get_me", {}) == "tool-ok"


async def test_github_handler_reports_missing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = github_client._make_handler("get_me")
    monkeypatch.setattr(github_client, "_client", None)
    assert await handler({}) == "Error: GitHub integration is not configured."


# ---------------------------------------------------------------- refresh_fundamentals


class _FakeFundamentalsProvider:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        if symbol in self.fail:
            raise RuntimeError("overview boom")
        return CompanyOverview(
            symbol=symbol,
            name=f"{symbol} Ltd",
            sector="IT",
            industry="Software",
            market_cap=1.5e12,  # float from yfinance — must be int()ed for BigInteger
            pe_ratio=25.0,
            pb_ratio=5.0,
            dividend_yield=0.01,
            eps=100.0,
            roe=0.4,
            week52_high=200.0,
            week52_low=100.0,
            summary=None,
        )

    async def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(
            symbol=symbol,
            name=f"{symbol} Ltd",
            exchange="NSE",
            price=150.0,
            currency="INR",
            day_change_pct=1.0,
            volume=42,
            as_of="2026-08-03T10:00:00+00:00",
        )


async def test_fetch_one_builds_upsert_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.nl2sql.jobs.refresh_fundamentals.provider", _FakeFundamentalsProvider()
    )
    row = await _fetch_one("TCS", asyncio.Semaphore(1))
    assert row is not None
    assert row["symbol"] == "TCS"
    assert row["market_cap"] == 1_500_000_000_000
    assert isinstance(row["market_cap"], int)
    assert row["current_price"] == 150.0
    assert row["volume"] == 42


async def test_fetch_one_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.nl2sql.jobs.refresh_fundamentals.provider",
        _FakeFundamentalsProvider(fail={"BAD"}),
    )
    sem = asyncio.Semaphore(2)
    results = await asyncio.gather(
        _fetch_one("OK1", sem), _fetch_one("BAD", sem), _fetch_one("OK2", sem)
    )
    assert results[0] is not None and results[2] is not None  # neighbors unaffected
    assert results[1] is None  # the failure became None, not an exception
