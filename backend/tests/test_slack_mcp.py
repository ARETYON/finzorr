"""Sanity: Slack MCP gating, allowlist, stable post-tool name, HITL wiring.
No network anywhere — fake MCP clients throughout."""

from typing import Any

import pytest

import app.mcp_client.slack_client as slack_client
from app.core.config import settings
from app.specialists.tools import _hitl_tools
from app.tools_registry.dispatcher import all_tools, dispatch

pytestmark = pytest.mark.sanity


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
            {
                "name": "list_channels",
                "description": "list channels",
                "inputSchema": {"type": "object"},
            },
            {
                "name": "admin_delete_channel",
                "description": "destructive",
                "inputSchema": {},
            },
            {
                "name": "post_message",
                "description": "post to a channel",
                "inputSchema": {
                    "type": "object",
                    "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["channel", "text"],
                },
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return "slack-ok"


class _BrokenMCPClient(_FakeMCPClient):
    async def initialize(self) -> None:
        raise RuntimeError("mcp unreachable")


async def test_slack_tools_absent_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(slack_client, "_client", None)
    assert await slack_client.register_slack_tools() == 0
    assert slack_client._client is None  # never even constructed


async def test_slack_unreachable_server_registers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_client, "_client", None)
    monkeypatch.setattr(slack_client, "MCPClient", _BrokenMCPClient)
    assert await slack_client.register_slack_tools() == 0


async def test_slack_registers_allowlisted_and_stable_post_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_client, "_client", None)
    monkeypatch.setattr(slack_client, "MCPClient", _FakeMCPClient)
    assert await slack_client.register_slack_tools() == 2
    names = {t.name for t in all_tools()}
    assert "slack_list_channels" in names
    assert "slack_admin_delete_channel" not in names  # non-allowlisted filtered
    assert "slack_post_message" in names  # stable curated name
    assert "slack_chat_post_message" not in names  # server-side name must not leak
    # read handler proxies through the MCP client with the server-side name
    assert await dispatch("slack_list_channels", {}) == "slack-ok"
    fake = slack_client._client
    assert isinstance(fake, _FakeMCPClient)
    assert fake.calls[-1][0] == "list_channels"
    # post handler dispatches under the stable name to the server-side name
    assert (
        await dispatch("slack_post_message", {"channel": "#general", "text": "hi"})
        == "slack-ok"
    )
    assert fake.calls[-1][0] == "post_message"


async def test_post_handler_caps_text(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeMCPClient("slack", "url", {})
    monkeypatch.setattr(slack_client, "_client", fake)
    handler = slack_client._make_post_handler("post_message")
    await handler({"channel": "#x", "text": "a" * 5000})
    assert len(fake.calls[-1][1]["text"]) == slack_client._POST_TEXT_MAX


async def test_slack_handler_reports_missing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = slack_client._make_handler("list_channels")
    monkeypatch.setattr(slack_client, "_client", None)
    assert await handler({}) == "Error: Slack integration is not configured."


def test_post_tool_requires_approval_by_default() -> None:
    # guards the HITL default in config.py — a write tool must stay gated
    assert "slack_post_message" in _hitl_tools()
