"""Slack MCP integration (token-gated; read allowlist + one approved write).

Connects to Slack's hosted MCP server, discovers tools, and registers a
curated subset into the shared dispatcher as `slack_*` tools. Read tools
follow the same read-only allowlist pattern as GitHub. The message-post tool
is this registry's ONE deliberate write-capable exception: it registers under
the stable name `slack_post_message` so the HITL approval gate (which matches
on registered names) can never be bypassed by a server-side rename.
Absent SLACK_BOT_TOKEN, nothing registers — graceful absence, never an error.
"""

from typing import Any

from app.core.config import settings
from app.core.logging import log
from app.infrastructure.llm.base import ToolDefinition
from app.mcp_client.base import MCPClient
from app.tools_registry.dispatcher import register_tool

SLACK_MCP_URL = "https://mcp.slack.com/mcp"

# Read-only tools (LLM08 excessive-agency guard). Names verified against the
# server's live tools/list; unknown names simply don't register (safe decay).
_READ_ALLOWLIST = {
    "search_messages",
    "list_channels",
    "get_channel_history",
    "get_thread_replies",
    "get_user_info",
}

# The write tool: first discovered name among these registers as the stable
# curated name `slack_post_message` (HITL-gated via settings.HITL_TOOLS).
_POST_TOOL_CANDIDATES = ("post_message", "send_message", "chat_post_message")
POST_TOOL_NAME = "slack_post_message"
_POST_TEXT_MAX = 4000

_client: MCPClient | None = None


async def register_slack_tools() -> int:
    """Discover + register curated Slack tools. Returns how many."""
    global _client  # noqa: PLW0603 — module singleton
    if not settings.SLACK_BOT_TOKEN:
        return 0
    _client = MCPClient(
        "slack", SLACK_MCP_URL, {"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"}
    )
    try:
        await _client.initialize()
        tools = await _client.list_tools()
    except Exception as exc:  # noqa: BLE001 — integration is optional
        log.warning("mcp.slack.unavailable", error=str(exc))
        return 0
    discovered = {str(t.get("name", "")): t for t in tools}
    log.info("mcp.slack.tools_discovered", names=sorted(discovered))
    count = 0
    for name, tool in discovered.items():
        if name not in _READ_ALLOWLIST:
            continue
        register_tool(
            ToolDefinition(
                name=f"slack_{name}",
                description=f"[Slack] {tool.get('description', name)}"[:1000],
                input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
            ),
            _make_handler(name),
        )
        count += 1
    for candidate in _POST_TOOL_CANDIDATES:
        if candidate not in discovered:
            continue
        tool = discovered[candidate]
        register_tool(
            ToolDefinition(
                name=POST_TOOL_NAME,
                description=(
                    "[Slack] Send a message to a Slack channel. This SENDS a real "
                    "message and requires user approval. "
                    + str(tool.get("description", ""))
                )[:1000],
                input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
            ),
            _make_post_handler(candidate),
        )
        count += 1
        break
    log.info("mcp.slack.registered", tools=count)
    return count


def _make_handler(tool_name: str):  # type: ignore[no-untyped-def]
    async def handler(arguments: dict[str, Any]) -> str:
        if _client is None:
            return "Error: Slack integration is not configured."
        return await _client.call_tool(tool_name, arguments)

    return handler


def _make_post_handler(tool_name: str):  # type: ignore[no-untyped-def]
    async def handler(arguments: dict[str, Any]) -> str:
        if _client is None:
            return "Error: Slack integration is not configured."
        text = arguments.get("text")
        if isinstance(text, str) and len(text) > _POST_TEXT_MAX:
            arguments = {**arguments, "text": text[:_POST_TEXT_MAX]}
        return await _client.call_tool(tool_name, arguments)

    return handler
