"""Tool registry + dispatcher.

House rules: the dispatcher NEVER raises — every failure returns an
LLM-visible error string so a tool outage degrades the answer instead of
crashing the turn. Tool families register handlers at import time; adding a
family never touches this file.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.base import ToolDefinition
from app.core.logging import log
from app.core.otel import span

ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]

_DEFAULT_TIMEOUT_S = 20.0
_definitions: dict[str, ToolDefinition] = {}
_handlers: dict[str, ToolHandler] = {}
_timeouts: dict[str, float] = {}


def register_tool(
    definition: ToolDefinition, handler: ToolHandler, *, timeout_s: float = _DEFAULT_TIMEOUT_S
) -> None:
    """Register one tool (schema + async handler returning a string).

    Slow tools (deep research, image generation, sandboxed code) declare their
    own budget here — a single global cap silently killed every tool whose
    honest runtime exceeded 20s.
    """
    _definitions[definition.name] = definition
    _handlers[definition.name] = handler
    _timeouts[definition.name] = timeout_s


def all_tools() -> list[ToolDefinition]:
    return list(_definitions.values())


async def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool; always returns a string, never raises."""
    handler = _handlers.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'."
    timeout_s = _timeouts.get(name, _DEFAULT_TIMEOUT_S)
    with span("tool", tool=name) as tool_span:
        try:
            result = await asyncio.wait_for(handler(arguments), timeout=timeout_s)
            tool_span.set_attribute("ok", not result.startswith("Error:"))
            return result
        except TimeoutError:
            log.warning("tool.timeout", tool=name)
            tool_span.set_attribute("ok", False)
            return f"Error: tool '{name}' timed out."
        except Exception as exc:  # noqa: BLE001 — the never-raise contract
            log.warning("tool.error", tool=name, error=str(exc))
            tool_span.set_attribute("ok", False)
            return f"Error: tool '{name}' failed — {type(exc).__name__}."


async def dispatch_all(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Run several tool calls concurrently (asyncio.gather)."""
    return list(await asyncio.gather(*(dispatch(name, args) for name, args in calls)))
