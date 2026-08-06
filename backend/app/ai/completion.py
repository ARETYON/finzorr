"""High-level completion helpers used by every graph node.

Adds what raw providers don't have:
- one bounded retry against `LLM_FALLBACK_PROVIDER` on failure (closes the
  reference implementation's known gap — its failover was config-time only),
- a per-provider daily token budget (Redis counter, best-effort) that shifts
  traffic down the free chain instead of silently failing when exhausted.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langsmith import get_current_run_tree, traceable

from app.ai.base import ChatMessage, StreamDone, TextDelta, ToolDefinition, Usage
from app.ai.registry import default_model, get_provider
from app.core.config import settings
from app.core.logging import log
from app.core.otel import span

OnToken = Callable[[str], Awaitable[None]]


def _budget_key(provider: str) -> str:
    return f"budget:{provider}:{datetime.now(UTC):%Y%m%d}"


async def _budget_exceeded(provider: str) -> bool:
    """True when today's token spend for a provider crossed the ceiling."""
    if settings.DAILY_TOKEN_BUDGET <= 0:
        return False
    try:
        from app.core.redis import get_redis

        used = await get_redis().get(_budget_key(provider))
        return int(used or 0) >= settings.DAILY_TOKEN_BUDGET
    except Exception:  # noqa: BLE001 — budget is best-effort, never blocks a turn
        return False


async def _record_usage(provider: str, usage: Usage) -> None:
    """Best-effort daily token accounting (48h TTL on the counter)."""
    if usage.total_tokens <= 0:
        return
    try:
        from app.core.redis import get_redis

        redis = get_redis()
        key = _budget_key(provider)
        await redis.incrby(key, usage.total_tokens)
        await redis.expire(key, 172800)
    except Exception:  # noqa: BLE001
        log.warning("ai.usage.record_failed", provider=provider)


# The single LLM choke point: the compiled graph traces itself into
# LangSmith (nodes included), but these calls are raw httpx — @traceable is
# what makes them appear as llm runs. It NO-OPS when tracing is disabled
# (no client, no network, ~0.1ms).
def _trim_llm_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run inputs minus the noise: on_token is a function repr, and `tools`
    would serialize the ENTIRE tool-schema list on every agent-loop call."""
    tools = inputs.get("tools")
    return {
        **{k: v for k, v in inputs.items() if k not in ("on_token", "tools")},
        "tools": [t.name for t in tools] if tools else None,
    }


@traceable(run_type="llm", name="llm.call", process_inputs=_trim_llm_inputs)
async def _run_stream(
    provider_name: str,
    messages: list[ChatMessage],
    *,
    model: str | None,
    tools: list[ToolDefinition] | None,
    temperature: float,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    on_token: OnToken | None,
) -> StreamDone:
    provider = get_provider(provider_name)
    resolved_model = model or default_model(provider.name)
    done: StreamDone | None = None
    with span("llm.call", provider=provider.name, model=resolved_model) as llm_span:
        async for event in provider.chat(
            messages,
            model=resolved_model,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        ):
            if isinstance(event, TextDelta) and on_token is not None:
                await on_token(event.text)
            elif isinstance(event, StreamDone):
                done = event
        if done is None:  # pragma: no cover — providers always emit StreamDone
            done = StreamDone(text="", tool_calls=[], usage=Usage())
        llm_span.set_attribute("prompt_tokens", done.usage.prompt_tokens)
        llm_span.set_attribute("completion_tokens", done.usage.completion_tokens)
        llm_span.set_attribute("tool_calls", len(done.tool_calls))
    run_tree = get_current_run_tree()
    if run_tree is not None:  # tracing on: token usage + model stats render
        run_tree.set(
            usage_metadata={
                "input_tokens": done.usage.prompt_tokens,
                "output_tokens": done.usage.completion_tokens,
                "total_tokens": done.usage.total_tokens,
            },
            metadata={"ls_provider": provider.name, "ls_model_name": resolved_model},
        )
    await _record_usage(provider.name, done.usage)
    log.info(
        "ai.call",
        provider=provider.name,
        model=resolved_model,
        prompt_tokens=done.usage.prompt_tokens,
        completion_tokens=done.usage.completion_tokens,
        tool_calls=len(done.tool_calls),
    )
    return done


async def stream(
    messages: list[ChatMessage],
    *,
    on_token: OnToken | None = None,
    model: str | None = None,
    provider: str | None = None,
    tools: list[ToolDefinition] | None = None,
    temperature: float = 0.6,
    max_tokens: int = 2048,
    response_format: dict[str, Any] | None = None,
    overall_timeout_s: float | None = None,
) -> StreamDone:
    """Stream a completion; retry once on the fallback provider if it throws.

    `overall_timeout_s` bounds the whole call INCLUDING the fallback retry —
    callers with their own budget (tools inside a dispatcher timeout) use it
    so nested budgets actually compose instead of each claiming the full
    per-request client timeout.
    """
    if overall_timeout_s is not None:
        async with asyncio.timeout(overall_timeout_s):
            return await stream(
                messages,
                on_token=on_token,
                model=model,
                provider=provider,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
    primary = provider or settings.LLM_PROVIDER
    if await _budget_exceeded(primary) and settings.LLM_FALLBACK_PROVIDER:
        log.warning("ai.budget.exceeded", provider=primary)
        primary = settings.LLM_FALLBACK_PROVIDER

    emitted = False

    async def _counting_on_token(text: str) -> None:
        nonlocal emitted
        emitted = True
        if on_token is not None:
            await on_token(text)

    try:
        return await _run_stream(
            primary,
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            on_token=_counting_on_token if on_token is not None else None,
        )
    except Exception as exc:
        fallback = settings.LLM_FALLBACK_PROVIDER
        if not fallback or fallback == primary:
            raise
        log.warning("ai.retry_fallback", failed=primary, fallback=fallback, error=str(exc))
        # If the primary already streamed partial text to the client, don't
        # stream the retry too — the user would watch the answer restart from
        # word one appended to the partial. The final `response` frame replaces
        # the streamed bubble with the fallback's complete text.
        return await _run_stream(
            fallback,
            messages,
            model=None,  # fallback provider uses its own default model
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            on_token=None if emitted else on_token,
        )


async def complete(
    messages: list[ChatMessage],
    *,
    model: str | None = None,
    provider: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    response_format: dict[str, Any] | None = None,
    overall_timeout_s: float | None = None,
) -> str:
    """Non-streaming convenience wrapper returning the full text."""
    done = await stream(
        messages,
        model=model,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        overall_timeout_s=overall_timeout_s,
    )
    return done.text
