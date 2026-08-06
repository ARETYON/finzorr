"""Tool registry + dispatcher.

House rules: the dispatcher NEVER raises — every failure returns an
LLM-visible error string so a tool outage degrades the answer instead of
crashing the turn. Tool families register handlers at import time; adding a
family never touches this file.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langsmith import traceable

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


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_arguments(definition: ToolDefinition, arguments: dict[str, Any]) -> str | None:
    """Model-supplied args checked against the declared schema BEFORE the
    handler runs — a contract at the boundary instead of per-handler
    defensive `str(args.get(...))`. Top-level required + primitive types only
    (deliberately minimal; no new dependency). Returns an error string or None.
    """
    schema = definition.input_schema or {}
    properties: dict[str, Any] = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in arguments or arguments[key] in (None, ""):
            return f"Error: invalid arguments for '{definition.name}' — '{key}' is required."
    for key, value in arguments.items():
        declared = properties.get(key, {}).get("type", "")
        expected = _JSON_TYPES.get(declared)
        if expected is None:
            continue
        # bool is an int subclass — True must not satisfy integer/number
        bad_bool = isinstance(value, bool) and declared in ("integer", "number")
        if bad_bool or not isinstance(value, expected):
            return (
                f"Error: invalid arguments for '{definition.name}' — "
                f"'{key}' must be {declared}."
            )
    return None


async def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool; always returns a string, never raises."""
    handler = _handlers.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'."
    if (validation_error := validate_arguments(_definitions[name], arguments)) is not None:
        log.warning("tool.bad_arguments", tool=name)
        return validation_error
    # each tool call is a first-class LangSmith run named tool.<name> —
    # langsmith_extra carries the dynamic name (traceable's name= is static)
    return await _dispatch_traced(
        name, arguments, langsmith_extra={"name": f"tool.{name}"}
    )


@traceable(run_type="tool")
async def _dispatch_traced(name: str, arguments: dict[str, Any]) -> str:
    """Timeout-bounded execution; the `Error:` prefix in the returned string
    is the failure signal (the never-raise contract keeps run status green —
    filter on output text, not status)."""
    handler = _handlers[name]
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
    """Run several tool calls concurrently, deduplicating identical calls.

    Models sometimes emit the same (name, args) twice in one batch — run it
    once and fan the result back to every requester."""
    import json

    keys = [
        (name, json.dumps(args, sort_keys=True, default=str)) for name, args in calls
    ]
    unique: dict[tuple[str, str], asyncio.Task[str]] = {}
    async with asyncio.TaskGroup() as group:
        for (name, args), key in zip(calls, keys, strict=True):
            if key not in unique:
                unique[key] = group.create_task(dispatch(name, args))
    return [unique[key].result() for key in keys]
