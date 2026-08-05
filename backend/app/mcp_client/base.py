"""Minimal MCP client over Streamable HTTP (JSON-RPC 2.0).

Speaks initialize / tools/list / tools/call — enough to consume any remote
MCP server. Responses may arrive as plain JSON or a single SSE event; both
shapes are handled.
"""

import json
from typing import Any

import httpx

from app.core.logging import log

_TIMEOUT_S = 30.0
PROTOCOL_VERSION = "2025-03-26"


class MCPError(Exception):
    """Raised on transport or JSON-RPC errors from an MCP server."""


def _parse_body(response: httpx.Response) -> dict[str, Any]:
    """Handle both application/json and text/event-stream single-event replies."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                parsed = json.loads(line[5:].strip())
                if isinstance(parsed, dict):
                    return parsed
        raise MCPError("no data event in SSE response")
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise MCPError("unexpected JSON-RPC body shape")
    return parsed


class MCPClient:
    """One remote MCP server connection (lazy initialize, cached session)."""

    def __init__(self, name: str, url: str, headers: dict[str, str]) -> None:
        self.name = name
        self._url = url
        self._headers = {
            **headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._session_id: str | None = None
        self._request_id = 0

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.post(
                self._url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": method,
                    "params": params or {},
                },
            )
            response.raise_for_status()
            if sid := response.headers.get("mcp-session-id"):
                self._session_id = sid
            body = _parse_body(response)
        if "error" in body:
            raise MCPError(f"{method}: {body['error']}")
        result = body.get("result", {})
        return result if isinstance(result, dict) else {}

    async def initialize(self) -> None:
        await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "finzorr", "version": "0.1.0"},
            },
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._rpc("tools/list")
        tools = result.get("tools", [])
        log.info("mcp.tools_discovered", server=self.name, count=len(tools))
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = result.get("content", [])
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(t for t in texts if t) or json.dumps(result)[:2000]
