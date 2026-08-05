"""GitHub MCP integration (token-gated, read-only allowlist).

Connects to GitHub's hosted MCP server, discovers tools, and registers a
curated read-only subset into the shared dispatcher as `github_*` tools.
Absent GITHUB_TOKEN, nothing registers — graceful absence, never an error.
"""

from typing import Any

from app.ai.base import ToolDefinition
from app.core.config import settings
from app.core.logging import log
from app.mcp_client.base import MCPClient
from app.tools_registry.dispatcher import register_tool

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"

# Read-only tools only (LLM08 excessive-agency guard) and a small set so the
# function-calling schema stays digestible for local models.
_ALLOWLIST = {
    "search_repositories",
    "search_code",
    "get_file_contents",
    "list_issues",
    "get_issue",
    "list_pull_requests",
    "get_pull_request",
    "get_me",
}

_client: MCPClient | None = None


async def register_github_tools() -> int:
    """Discover + register allowlisted GitHub tools. Returns how many."""
    global _client  # noqa: PLW0603 — module singleton
    if not settings.GITHUB_TOKEN:
        return 0
    _client = MCPClient(
        "github", GITHUB_MCP_URL, {"Authorization": f"Bearer {settings.GITHUB_TOKEN}"}
    )
    try:
        await _client.initialize()
        tools = await _client.list_tools()
    except Exception as exc:  # noqa: BLE001 — integration is optional
        log.warning("mcp.github.unavailable", error=str(exc))
        return 0
    count = 0
    for tool in tools:
        name = str(tool.get("name", ""))
        if name not in _ALLOWLIST:
            continue
        register_tool(
            ToolDefinition(
                name=f"github_{name}",
                description=f"[GitHub] {tool.get('description', name)}"[:1000],
                input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
            ),
            _make_handler(name),
        )
        count += 1
    log.info("mcp.github.registered", tools=count)
    return count


def _make_handler(tool_name: str):  # type: ignore[no-untyped-def]
    async def handler(arguments: dict[str, Any]) -> str:
        if _client is None:
            return "Error: GitHub integration is not configured."
        return await _client.call_tool(tool_name, arguments)

    return handler
