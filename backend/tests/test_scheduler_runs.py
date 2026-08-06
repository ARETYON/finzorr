"""Integration: scheduler briefing/alert/task runs over a real Postgres.

Redis is a fake (dedupe protocol only), the market provider and the graph
turn are monkeypatched — no network, no LLM. What IS real: SessionLocal,
find-or-create sessions, message posting, alert row locking.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.web_search import SearchResult
from app.db.session import SessionLocal
from app.market_data.base import QuoteData
from app.models.chat_session import ChatSession
from app.models.message import Message
from app.models.price_alert import PriceAlert
from app.models.scheduled_task import ScheduledTask
from app.models.user import User
from app.models.watchlist_item import WatchlistItem
from app.scheduler import (
    BRIEFING_TITLE,
    IST,
    _run_alerts,
    _run_briefings,
    _run_tasks,
)

pytestmark = pytest.mark.integration

MONDAY_OPEN = datetime(2026, 8, 3, 10, 0, tzinfo=IST)  # Monday, NSE open
SATURDAY = datetime(2026, 8, 1, 10, 0, tzinfo=IST)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    async def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1 if self.store.pop(key, None) is not None else 0


class FakeQuoteProvider:
    def __init__(self, prices: dict[str, float], fail: set[str] | None = None) -> None:
        self.prices = prices
        self.fail = fail or set()

    async def get_quote(self, symbol: str) -> QuoteData:
        if symbol in self.fail:
            raise RuntimeError("quote boom")
        return QuoteData(
            symbol=symbol,
            name=symbol,
            exchange="NSE",
            price=self.prices[symbol],
            currency="INR",
            day_change_pct=1.5,
            volume=100,
            as_of="2026-08-03T10:00:00+05:30",
        )


def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    return fake


async def _make_user() -> uuid.UUID:
    tag = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        user = User(google_sub=f"sched-{tag}", email=f"sched-{tag}@localhost", name="Sched User")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


async def _messages_for(user_id: uuid.UUID, title: str) -> list[Message]:
    async with SessionLocal() as db:
        session_row = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.user_id == user_id, ChatSession.title == title
                )
            )
        ).scalars().first()
        if session_row is None:
            return []
        return list(
            (
                await db.execute(select(Message).where(Message.session_id == session_row.id))
            ).scalars()
        )


# ---------------------------------------------------------------- briefings


async def test_briefing_posts_watchlist_summary(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        db.add(WatchlistItem(user_id=user_id, symbol="TCS"))
        db.add(WatchlistItem(user_id=user_id, symbol="BROKEN"))
        await db.commit()
    _fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "BRIEFING_TIME_IST", "08:30")
    monkeypatch.setattr(
        "app.market_data.yfinance_provider.provider",
        FakeQuoteProvider({"TCS": 4100.5}, fail={"BROKEN"}),
    )

    async def fake_search(query: str, max_results: int = 6) -> tuple[list[SearchResult], str]:
        return [SearchResult("Nifty up", "https://example.com/n", "snippet")], "fake"

    monkeypatch.setattr("app.core.web_search.search", fake_search)

    await _run_briefings(MONDAY_OPEN)

    messages = await _messages_for(user_id, BRIEFING_TITLE)
    assert len(messages) == 1
    body = messages[0].content
    assert messages[0].route == "briefing"
    assert "TCS" in body and "4,100.50" in body
    assert "**BROKEN** — quote unavailable" in body
    assert "Headlines" in body and "https://example.com/n" in body


async def test_briefing_deduped_within_a_day(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        db.add(WatchlistItem(user_id=user_id, symbol="TCS"))
        await db.commit()
    _fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "BRIEFING_TIME_IST", "08:30")
    monkeypatch.setattr(
        "app.market_data.yfinance_provider.provider", FakeQuoteProvider({"TCS": 100.0})
    )

    async def no_news(query: str, max_results: int = 6) -> tuple[list[SearchResult], str]:
        return [], "none"

    monkeypatch.setattr("app.core.web_search.search", no_news)

    await _run_briefings(MONDAY_OPEN)
    await _run_briefings(MONDAY_OPEN)  # same day: the Redis day-key dedupes
    assert len(await _messages_for(user_id, BRIEFING_TITLE)) == 1


async def test_briefing_failure_releases_key_for_retry(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        db.add(WatchlistItem(user_id=user_id, symbol="TCS"))
        await db.commit()
    fake = _fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "BRIEFING_TIME_IST", "08:30")

    async def broken_build(user_id: uuid.UUID, symbols: list[str]) -> str:
        raise RuntimeError("briefing boom")

    monkeypatch.setattr("app.scheduler._build_briefing", broken_build)

    await _run_briefings(MONDAY_OPEN)

    key = f"briefing:{user_id}:{MONDAY_OPEN:%Y%m%d}"
    assert key in fake.deleted  # released so the next tick retries
    assert await _messages_for(user_id, BRIEFING_TITLE) == []


# ---------------------------------------------------------------- alerts


async def test_alert_fires_deactivates_and_posts(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        alert = PriceAlert(user_id=user_id, symbol="TCS", direction="above", target=100.0)
        db.add(alert)
        await db.commit()
        await db.refresh(alert)
    _fake_redis(monkeypatch)
    monkeypatch.setattr(
        "app.market_data.yfinance_provider.provider", FakeQuoteProvider({"TCS": 150.0})
    )

    await _run_alerts(MONDAY_OPEN)

    async with SessionLocal() as db:
        row = await db.get(PriceAlert, alert.id)
    assert row is not None and row.active is False and row.triggered_at is not None
    messages = await _messages_for(user_id, BRIEFING_TITLE)
    assert len(messages) == 1
    assert messages[0].route == "alert"
    assert "Price alert" in messages[0].content and "150.00" in messages[0].content


async def test_alert_not_crossed_stays_active(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        alert = PriceAlert(user_id=user_id, symbol="TCS", direction="below", target=100.0)
        db.add(alert)
        await db.commit()
        await db.refresh(alert)
    _fake_redis(monkeypatch)
    monkeypatch.setattr(
        "app.market_data.yfinance_provider.provider", FakeQuoteProvider({"TCS": 150.0})
    )

    await _run_alerts(MONDAY_OPEN)

    async with SessionLocal() as db:
        row = await db.get(PriceAlert, alert.id)
    assert row is not None and row.active is True and row.triggered_at is None
    assert await _messages_for(user_id, BRIEFING_TITLE) == []


async def test_alerts_noop_when_market_closed(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_redis() -> FakeRedis:
        raise AssertionError("closed market must return before touching Redis")

    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)
    await _run_alerts(SATURDAY)


async def test_alerts_window_deduped(_database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        db.add(PriceAlert(user_id=user_id, symbol="TCS", direction="above", target=100.0))
        await db.commit()
    _fake_redis(monkeypatch)
    monkeypatch.setattr(
        "app.market_data.yfinance_provider.provider", FakeQuoteProvider({"TCS": 150.0})
    )

    await _run_alerts(MONDAY_OPEN)
    await _run_alerts(MONDAY_OPEN)  # same 5-min window: checked exactly once
    assert len(await _messages_for(user_id, BRIEFING_TITLE)) == 1


# ---------------------------------------------------------------- tasks


async def test_due_task_runs_turn_in_own_session(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        task = ScheduledTask(user_id=user_id, spec="daily@00:00", prompt="check the markets")
        db.add(task)
        await db.commit()
        await db.refresh(task)
    _fake_redis(monkeypatch)
    calls: list[tuple[uuid.UUID, uuid.UUID, str, str]] = []

    origins: list[str] = []

    async def fake_run_turn(
        session_id: uuid.UUID,
        user_id_: uuid.UUID,
        user_name: str,
        prompt: str,
        origin: str = "chat",
    ) -> None:
        calls.append((session_id, user_id_, user_name, prompt))
        origins.append(origin)

    monkeypatch.setattr("app.graph.turn.run_turn", fake_run_turn)

    await _run_tasks(MONDAY_OPEN)

    assert len(calls) == 1
    assert calls[0][1] == user_id
    assert calls[0][2] == "Sched User"
    assert origins == ["scheduled"]  # agent-initiated traffic is tagged
    assert calls[0][3] == "check the markets"
    async with SessionLocal() as db:
        row = await db.get(ScheduledTask, task.id)
        session_row = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.user_id == user_id, ChatSession.title == "⏰ check the markets"
                )
            )
        ).scalars().first()
    assert row is not None and row.last_run_at is not None
    assert session_row is not None and session_row.id == calls[0][0]

    await _run_tasks(MONDAY_OPEN)  # same day: day-key dedupes
    assert len(calls) == 1


async def test_not_due_or_inactive_tasks_skipped(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        db.add(ScheduledTask(user_id=user_id, spec="daily@23:59", prompt="later"))
        db.add(ScheduledTask(user_id=user_id, spec="daily@00:00", prompt="off", active=False))
        await db.commit()
    _fake_redis(monkeypatch)

    async def fail_run_turn(
        session_id: uuid.UUID, user_id_: uuid.UUID, user_name: str, prompt: str
    ) -> None:
        raise AssertionError("no task should run")

    monkeypatch.setattr("app.graph.turn.run_turn", fail_run_turn)
    await _run_tasks(MONDAY_OPEN)


async def test_task_failure_releases_key_and_keeps_last_run_unset(
    _database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _make_user()
    async with SessionLocal() as db:
        task = ScheduledTask(user_id=user_id, spec="daily@00:00", prompt="boom task")
        db.add(task)
        await db.commit()
        await db.refresh(task)
    fake = _fake_redis(monkeypatch)

    async def broken_run_turn(
        session_id: uuid.UUID, user_id_: uuid.UUID, user_name: str, prompt: str
    ) -> None:
        raise RuntimeError("turn boom")

    monkeypatch.setattr("app.graph.turn.run_turn", broken_run_turn)

    await _run_tasks(MONDAY_OPEN)

    assert f"task:{task.id}:{MONDAY_OPEN:%Y%m%d}" in fake.deleted
    async with SessionLocal() as db:
        row = await db.get(ScheduledTask, task.id)
    assert row is not None and row.last_run_at is None
