"""Dev-only diagnostics. Mounted only when APP_ENV=dev."""

from typing import Any

from fastapi import APIRouter

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
