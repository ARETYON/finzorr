"""Dev-only diagnostics. Mounted only when APP_ENV=dev."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.ai.base import SystemMessage, ToolDefinition, UserMessage
from app.ai.completion import stream
from app.ai.registry import available_providers

router = APIRouter(prefix="/api/debug", tags=["debug"])

_PING_TOOL = ToolDefinition(
    name="get_current_time",
    description="Get the current time for a city.",
    input_schema={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
)


@router.get("/echo")
async def echo(message: str) -> dict[str, str]:
    """Target for the worked-example microservice tool config."""
    return {"echo": message}


@router.get("/route/{route_name}")
async def run_route(route_name: str, q: str) -> dict[str, Any]:
    """Run one specialist node standalone (no supervisor, no persistence)."""
    from app.graph.state import AssistantState

    nodes: dict[str, Any] = {}
    from app.graph.nodes.general_chat import general_chat_node

    nodes["general_chat"] = general_chat_node
    from app.graph.nodes.tools import tools_node

    nodes["tools"] = tools_node
    try:
        from app.graph.nodes.nl2sql import nl2sql_node

        nodes["nl2sql"] = nl2sql_node
    except ImportError:
        pass
    try:
        from app.graph.nodes.rag import rag_node

        nodes["rag"] = rag_node
    except ImportError:
        pass
    try:
        from app.graph.nodes.web_search import web_search_node

        nodes["web_search"] = web_search_node
    except ImportError:
        pass
    try:
        from app.graph.nodes.memory import memory_node

        nodes["memory"] = memory_node
    except ImportError:
        pass
    node = nodes.get(route_name)
    if node is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"unknown route '{route_name}' — available: {sorted(nodes)}",
        )
    state: AssistantState = {
        "session_id": "debug",
        "user_id": "debug",
        "user_name": "Dev",
        "user_msg": q,
        "messages": [],
    }
    result = await node(state)
    return {k: v for k, v in result.items()}


@router.get("/llm-ping")
async def llm_ping() -> dict[str, Any]:
    """Exercise streaming + tool-calling against the active provider."""
    tokens: list[str] = []

    async def collect(t: str) -> None:
        tokens.append(t)

    stream_done = await stream(
        [
            SystemMessage(content="You are a concise assistant."),
            UserMessage(content="Reply with exactly: pong"),
        ],
        on_token=collect,
        temperature=0.0,
        max_tokens=20,
    )
    tool_done = await stream(
        [
            SystemMessage(content="Use the provided tool when asked about time."),
            UserMessage(content="What time is it in Mumbai right now?"),
        ],
        tools=[_PING_TOOL],
        temperature=0.0,
        max_tokens=200,
    )
    return {
        "providers": available_providers(),
        "streaming": {"text": stream_done.text, "token_chunks": len(tokens)},
        "tool_calling": {
            "tool_calls": [
                {"name": tc.name, "arguments": tc.arguments} for tc in tool_done.tool_calls
            ],
            "text": tool_done.text[:200],
        },
        "usage_total": stream_done.usage.total_tokens + tool_done.usage.total_tokens,
    }
