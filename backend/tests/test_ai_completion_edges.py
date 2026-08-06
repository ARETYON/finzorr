"""Sanity: completion budget accounting + fallback edges (fake providers/redis)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

import app.ai.completion as completion
from app.ai.base import (
    ChatMessage,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolDefinition,
    Usage,
    UserMessage,
)
from app.core.config import settings

pytestmark = pytest.mark.sanity

MESSAGES: list[ChatMessage] = [UserMessage(content="hi")]


class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = store or {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def incrby(self, key: str, amount: int) -> int:
        value = int(self.store.get(key, "0")) + amount
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, ttl_s: int) -> bool:
        self.ttls[key] = ttl_s
        return True


class FakeProvider:
    """Async-generator chat double; optionally emits some events then raises."""

    def __init__(
        self,
        name: str,
        events: list[StreamEvent] | None = None,
        error: Exception | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.name = name
        self.events = events or []
        self.error = error
        self.delay_s = delay_s
        self.calls = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.6,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


def _done(text: str, prompt: int = 0, comp: int = 0) -> StreamDone:
    return StreamDone(
        text=text, tool_calls=[], usage=Usage(prompt_tokens=prompt, completion_tokens=comp)
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    providers: dict[str, FakeProvider],
    redis: FakeRedis | None = None,
) -> FakeRedis:
    fake_redis = redis or FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake_redis)
    monkeypatch.setattr(completion, "get_provider", lambda name=None: providers[name])
    monkeypatch.setattr(completion, "default_model", lambda name: "fake-model")
    return fake_redis


def _today_key(provider: str) -> str:
    return f"budget:{provider}:{datetime.now(UTC):%Y%m%d}"


# ---------------------------------------------------------------- budget checks


def test_budget_key_is_per_provider_per_day() -> None:
    assert completion._budget_key("groq") == _today_key("groq")
    assert completion._budget_key("groq") != completion._budget_key("ollama")


async def test_budget_disabled_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)

    def _boom() -> FakeRedis:
        raise AssertionError("budget=0 must short-circuit before Redis")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)
    assert await completion._budget_exceeded("groq") is False


async def test_budget_exceeded_at_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 100)
    fake = FakeRedis({_today_key("groq"): "100"})
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    assert await completion._budget_exceeded("groq") is True


async def test_budget_not_exceeded_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 100)
    fake = FakeRedis({_today_key("groq"): "99"})
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    assert await completion._budget_exceeded("groq") is False
    assert await completion._budget_exceeded("gemini") is False  # no spend recorded


async def test_budget_fails_open_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 100)

    def _boom() -> FakeRedis:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)
    assert await completion._budget_exceeded("groq") is False


# ---------------------------------------------------------------- _record_usage


async def test_record_usage_skips_zero_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> FakeRedis:
        raise AssertionError("zero usage must not touch Redis")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)
    await completion._record_usage("groq", Usage())


async def test_record_usage_increments_with_48h_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    await completion._record_usage("groq", Usage(prompt_tokens=30, completion_tokens=12))
    await completion._record_usage("groq", Usage(prompt_tokens=8, completion_tokens=0))
    key = _today_key("groq")
    assert fake.store[key] == "50"
    assert fake.ttls[key] == 172800


async def test_record_usage_swallows_redis_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> FakeRedis:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)
    await completion._record_usage("groq", Usage(prompt_tokens=1, completion_tokens=1))


# ---------------------------------------------------------------- stream paths


async def test_stream_happy_path_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    events: list[StreamEvent] = [TextDelta("he"), TextDelta("y"), _done("hey", 7, 3)]
    providers = {"groq": FakeProvider("groq", events)}
    fake = _wire(monkeypatch, providers)
    tokens: list[str] = []

    async def on_token(text: str) -> None:
        tokens.append(text)

    result = await completion.stream(MESSAGES, provider="groq", on_token=on_token)
    assert result.text == "hey"
    assert tokens == ["he", "y"]
    assert fake.store[_today_key("groq")] == "10"


async def test_budget_exceeded_switches_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 100)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "ollama")
    providers = {
        "groq": FakeProvider("groq", [_done("from groq")]),
        "ollama": FakeProvider("ollama", [_done("from ollama")]),
    }
    _wire(monkeypatch, providers, FakeRedis({_today_key("groq"): "100"}))
    result = await completion.stream(MESSAGES, provider="groq")
    assert result.text == "from ollama"
    assert providers["groq"].calls == 0
    assert providers["ollama"].calls == 1


async def test_budget_exceeded_without_fallback_stays_on_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 100)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    providers = {"groq": FakeProvider("groq", [_done("from groq")])}
    _wire(monkeypatch, providers, FakeRedis({_today_key("groq"): "100"}))
    result = await completion.stream(MESSAGES, provider="groq")
    assert result.text == "from groq"
    assert providers["groq"].calls == 1


async def test_failure_retries_on_fallback_without_restreaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "ollama")
    providers = {
        "groq": FakeProvider("groq", [TextDelta("par")], error=RuntimeError("groq boom")),
        "ollama": FakeProvider("ollama", [TextDelta("full"), _done("full answer")]),
    }
    _wire(monkeypatch, providers)
    tokens: list[str] = []

    async def on_token(text: str) -> None:
        tokens.append(text)

    result = await completion.stream(MESSAGES, provider="groq", on_token=on_token)
    assert result.text == "full answer"
    # the partial primary stream must NOT be followed by the fallback's tokens
    assert tokens == ["par"]
    assert providers["ollama"].calls == 1


async def test_failure_streams_fallback_when_nothing_was_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "ollama")
    providers = {
        "groq": FakeProvider("groq", error=RuntimeError("groq boom")),
        "ollama": FakeProvider("ollama", [TextDelta("full"), _done("full answer")]),
    }
    _wire(monkeypatch, providers)
    tokens: list[str] = []

    async def on_token(text: str) -> None:
        tokens.append(text)

    result = await completion.stream(MESSAGES, provider="groq", on_token=on_token)
    assert result.text == "full answer"
    assert tokens == ["full"]


async def test_failure_without_fallback_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    providers = {"groq": FakeProvider("groq", error=RuntimeError("groq boom"))}
    _wire(monkeypatch, providers)
    with pytest.raises(RuntimeError, match="groq boom"):
        await completion.stream(MESSAGES, provider="groq")


async def test_failure_when_fallback_equals_primary_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    providers = {"groq": FakeProvider("groq", error=RuntimeError("groq boom"))}
    _wire(monkeypatch, providers)
    with pytest.raises(RuntimeError, match="groq boom"):
        await completion.stream(MESSAGES, provider="groq")
    assert providers["groq"].calls == 1  # no self-retry


async def test_overall_timeout_bounds_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    providers = {"groq": FakeProvider("groq", [_done("late")], delay_s=5.0)}
    _wire(monkeypatch, providers)
    with pytest.raises(TimeoutError):
        await completion.stream(MESSAGES, provider="groq", overall_timeout_s=0.05)


async def test_overall_timeout_returns_result_when_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    providers = {"groq": FakeProvider("groq", [_done("quick")])}
    _wire(monkeypatch, providers)
    result = await completion.stream(MESSAGES, provider="groq", overall_timeout_s=5.0)
    assert result.text == "quick"


async def test_complete_returns_full_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    providers = {"groq": FakeProvider("groq", [TextDelta("42"), _done("42")])}
    _wire(monkeypatch, providers)
    assert await completion.complete(MESSAGES, provider="groq") == "42"
