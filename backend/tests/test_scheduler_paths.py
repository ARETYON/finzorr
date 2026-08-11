"""Sanity: scheduler pure helpers + Redis dedupe paths (no live deps)."""

from datetime import datetime, time

import pytest

from app.core.config import settings
from app.models.scheduled_task import ScheduledTask
from app.scheduler import (
    IST,
    _already,
    _market_open,
    _parse_hhmm,
    _release,
    _run_briefings,
    _task_due,
)

pytestmark = pytest.mark.sanity


class FakeRedis:
    """Minimal async Redis double for the dedupe key protocol."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []
        self.ttls: dict[str, int] = {}

    async def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1 if self.store.pop(key, None) is not None else 0


def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr("app.infrastructure.redis.get_redis", lambda: fake)
    return fake


# ---------------------------------------------------------------- _parse_hhmm


def test_parse_hhmm_valid() -> None:
    assert _parse_hhmm("08:30") == time(8, 30)
    assert _parse_hhmm("0:0") == time(0, 0)
    assert _parse_hhmm("23:59") == time(23, 59)


def test_parse_hhmm_invalid_returns_none() -> None:
    assert _parse_hhmm("nonsense") is None
    assert _parse_hhmm("25:00") is None
    assert _parse_hhmm("08:60") is None
    assert _parse_hhmm("0830") is None
    assert _parse_hhmm("") is None


# ---------------------------------------------------------------- _market_open


def test_market_open_weekday_within_hours() -> None:
    # 2026-08-03 is a Monday
    assert _market_open(datetime(2026, 8, 3, 10, 0, tzinfo=IST))
    assert _market_open(datetime(2026, 8, 3, 9, 15, tzinfo=IST))  # open boundary
    assert _market_open(datetime(2026, 8, 3, 15, 30, tzinfo=IST))  # close boundary


def test_market_closed_outside_hours_and_weekends() -> None:
    assert not _market_open(datetime(2026, 8, 3, 9, 14, tzinfo=IST))
    assert not _market_open(datetime(2026, 8, 3, 15, 31, tzinfo=IST))
    assert not _market_open(datetime(2026, 8, 1, 10, 0, tzinfo=IST))  # Saturday
    assert not _market_open(datetime(2026, 8, 2, 10, 0, tzinfo=IST))  # Sunday


# ---------------------------------------------------------------- _task_due


def _task(spec: str) -> ScheduledTask:
    return ScheduledTask(spec=spec, prompt="p")


def test_task_due_daily_at_or_after_target() -> None:
    now = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
    assert _task_due(_task("daily@09:00"), now)
    assert _task_due(_task("daily@08:00"), now)
    assert not _task_due(_task("daily@09:01"), now)


def test_task_due_weekly_requires_matching_weekday() -> None:
    monday = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
    assert _task_due(_task("weekly@0@09:00"), monday)
    assert not _task_due(_task("weekly@1@09:00"), monday)  # Tuesday task
    assert not _task_due(_task("weekly@0@11:00"), monday)  # later today


def test_task_due_malformed_specs_never_fire() -> None:
    now = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
    assert not _task_due(_task("hourly@09:00"), now)
    assert not _task_due(_task("daily@junk"), now)
    assert not _task_due(_task("weekly@x@09:00"), now)  # ValueError branch
    assert not _task_due(_task("daily"), now)
    assert not _task_due(_task(""), now)


# ---------------------------------------------------------------- _already / _release


async def test_already_first_claim_wins_then_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_redis(monkeypatch)
    assert await _already("k1") is False  # first claim
    assert await _already("k1") is True  # already fired
    assert fake.ttls["k1"] == 25 * 3600


async def test_already_custom_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_redis(monkeypatch)
    assert await _already("k2", ttl_s=330) is False
    assert fake.ttls["k2"] == 330


async def test_already_fails_closed_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> FakeRedis:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.infrastructure.redis.get_redis", _boom)
    assert await _already("k3") is True  # skip work rather than duplicate


async def test_release_frees_key_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_redis(monkeypatch)
    assert await _already("k4") is False
    await _release("k4")
    assert fake.deleted == ["k4"]
    assert await _already("k4") is False  # claimable again


async def test_release_swallows_redis_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> FakeRedis:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.infrastructure.redis.get_redis", _boom)
    await _release("k5")  # must not raise


# ---------------------------------------------------------------- _run_briefings gating


async def test_briefings_noop_before_target_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BRIEFING_TIME_IST", "23:59")

    def _no_db() -> None:
        raise AssertionError("SessionLocal must not be touched before the target time")

    monkeypatch.setattr("app.scheduler.SessionLocal", _no_db)
    await _run_briefings(datetime(2026, 8, 3, 8, 0, tzinfo=IST))


async def test_briefings_noop_on_unparseable_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BRIEFING_TIME_IST", "bogus")

    def _no_db() -> None:
        raise AssertionError("SessionLocal must not be touched with a bad target")

    monkeypatch.setattr("app.scheduler.SessionLocal", _no_db)
    await _run_briefings(datetime(2026, 8, 3, 8, 0, tzinfo=IST))
