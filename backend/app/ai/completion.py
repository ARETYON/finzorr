"""High-level completion helpers used by every graph node.

Adds what raw providers don't have:
- one bounded retry against `LLM_FALLBACK_PROVIDER` on failure (closes the
  reference implementation's known gap — its failover was config-time only),
- a per-provider daily token budget (Redis counter, best-effort) that shifts
  traffic down the free chain instead of silently failing when exhausted.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.ai.base import ChatMessage, StreamDone, TextDelta, ToolDefinition, Usage
from app.ai.registry import default_model, get_provider
from app.core.config import settings
from app.core.logging import log

OnToken = Callable[[str], Awaitable[None]]


def _budget_key(provider: str) -> str:
    return f"budget:{provider}:{datetime.now(UTC):%Y%m%d}"


async def _budget_exceeded(provider: str) -> bool:
    """True when today's token spend for a provider crossed the ceiling."""
    if settings.DAILY_TOKEN_BUDGET <= 0:
        return False
    try:
        from app.services.redis_client import get_redis

        used = await get_redis().get(_budget_key(provider))
        return int(used or 0) >= settings.DAILY_TOKEN_BUDGET
    except Exception:  # noqa: BLE001 — budget is best-effort, never blocks a turn
        return False


async def _record_usage(provider: str, usage: Usage) -> None:
    """Best-effort daily token accounting (48h TTL on the counter)."""
    if usage.total_tokens <= 0:
        return
    try:
        from app.services.redis_client import get_redis

        redis = get_redis()
        key = _budget_key(provider)
        await redis.incrby(key, usage.total_tokens)
        await redis.expire(key, 172800)
    except Exception:  # noqa: BLE001
        log.warning("ai.usage.record_failed", provider=provider)


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
) -> StreamDone:
    """Stream a completion; retry once on the fallback provider if it throws."""
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
) -> str:
    """Non-streaming convenience wrapper returning the full text."""
    done = await stream(
        messages,
        model=model,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
    )
    return done.text
