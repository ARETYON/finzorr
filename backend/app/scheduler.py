"""In-process scheduler: daily briefings, price alerts, scheduled tasks.

One asyncio loop (started in the app lifespan) ticks every minute. All three
jobs are best-effort and per-item isolated — one failure never kills the loop.
Redis keys dedupe "already ran today". IST is the reference timezone.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import log
from app.db.session import SessionLocal
from app.models.chat_session import ChatSession
from app.models.message import Message
from app.models.price_alert import PriceAlert
from app.models.scheduled_task import ScheduledTask
from app.models.user import User, utcnow
from app.models.watchlist_item import WatchlistItem

IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60
ALERT_INTERVAL_MIN = 5
BRIEFING_TITLE = "📅 Daily Briefing"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _market_open(now: datetime) -> bool:
    """Rough NSE hours: Mon-Fri 09:15-15:30 IST."""
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)


async def _already(key: str, ttl_s: int = 90000) -> bool:
    """True if `key` fired already (sets it atomically otherwise)."""
    try:
        from app.services.redis_client import get_redis

        return not await get_redis().set(key, "1", nx=True, ex=ttl_s)
    except Exception:  # noqa: BLE001 — fail closed (skip) to avoid duplicates
        return True


async def _briefing_session(db, user_id: uuid.UUID) -> ChatSession:  # type: ignore[no-untyped-def]
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id, ChatSession.title == BRIEFING_TITLE
        )
    )
    session = result.scalars().first()
    if session is None:
        session = ChatSession(user_id=user_id, title=BRIEFING_TITLE)
        db.add(session)
        await db.flush()
    return session


async def _post_message(user_id: uuid.UUID, content: str, route: str) -> None:
    async with SessionLocal() as db:
        session = await _briefing_session(db, user_id)
        db.add(Message(session_id=session.id, role="assistant", content=content, route=route))
        session.updated_at = utcnow()
        await db.commit()


async def _build_briefing(user_id: uuid.UUID, symbols: list[str]) -> str:
    from app.market_data.yfinance_provider import provider

    lines = [f"## Good morning — your watchlist briefing ({_ist_now():%d %b %Y})", ""]
    for symbol in symbols[:10]:
        try:
            q = await provider.get_quote(symbol)
            arrow = "🔺" if (q.day_change_pct or 0) >= 0 else "🔻"
            lines.append(
                f"- **{q.symbol}** ₹{q.price:,.2f} {arrow} {q.day_change_pct or 0:+.2f}%"
            )
        except Exception:  # noqa: BLE001
            lines.append(f"- **{symbol}** — quote unavailable")
    import contextlib

    with contextlib.suppress(Exception):
        from app.core.web_search import search as web_search

        results, _provider = await web_search("Indian stock market today Nifty Sensex", 3)
        if results:
            lines += ["", "**Headlines:**"] + [f"- [{r.title}]({r.url})" for r in results]
    lines += ["", "_Data may be delayed — not investment advice._"]
    return "\n".join(lines)


async def _run_briefings(now: datetime) -> None:
    target = settings.BRIEFING_TIME_IST
    if f"{now:%H:%M}" != target:
        return
    async with SessionLocal() as db:
        result = await db.execute(select(WatchlistItem.user_id).distinct())
        user_ids = [row[0] for row in result]
    for user_id in user_ids:
        if await _already(f"briefing:{user_id}:{now:%Y%m%d}"):
            continue
        try:
            async with SessionLocal() as db:
                symbols_result = await db.execute(
                    select(WatchlistItem.symbol).where(WatchlistItem.user_id == user_id)
                )
                symbols = [row[0] for row in symbols_result]
            content = await _build_briefing(user_id, symbols)
            await _post_message(user_id, content, "briefing")
            log.info("scheduler.briefing_sent", user_id=str(user_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler.briefing_failed", user_id=str(user_id), error=str(exc))


async def _run_alerts(now: datetime) -> None:
    if not _market_open(now) or now.minute % ALERT_INTERVAL_MIN != 0:
        return
    from app.market_data.yfinance_provider import provider

    async with SessionLocal() as db:
        result = await db.execute(select(PriceAlert).where(PriceAlert.active.is_(True)))
        alerts = list(result.scalars())
    for alert in alerts:
        try:
            quote = await provider.get_quote(alert.symbol)
            crossed = (alert.direction == "above" and quote.price >= alert.target) or (
                alert.direction == "below" and quote.price <= alert.target
            )
            if not crossed:
                continue
            async with SessionLocal() as db:
                row = await db.get(PriceAlert, alert.id)
                if row is None or not row.active:
                    continue
                row.active = False
                row.triggered_at = datetime.now(UTC)
                await db.commit()
            await _post_message(
                alert.user_id,
                f"🔔 **Price alert:** {alert.symbol} is now ₹{quote.price:,.2f} "
                f"({alert.direction} your target of ₹{alert.target:,.2f}).",
                "alert",
            )
            log.info("scheduler.alert_fired", symbol=alert.symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler.alert_failed", symbol=alert.symbol, error=str(exc))


def _task_due(task: ScheduledTask, now: datetime) -> bool:
    parts = task.spec.split("@")
    try:
        if parts[0] == "daily" and len(parts) == 2:
            return f"{now:%H:%M}" == parts[1]
        if parts[0] == "weekly" and len(parts) == 3:
            return now.weekday() == int(parts[1]) and f"{now:%H:%M}" == parts[2]
    except ValueError:
        return False
    return False


async def _run_tasks(now: datetime) -> None:
    async with SessionLocal() as db:
        result = await db.execute(select(ScheduledTask).where(ScheduledTask.active.is_(True)))
        tasks = list(result.scalars())
    for task in tasks:
        if not _task_due(task, now) or await _already(f"task:{task.id}:{now:%Y%m%d%H%M}"):
            continue
        try:
            async with SessionLocal() as db:
                session = await _briefing_session(db, task.user_id)
                await db.commit()
                session_id = session.id
                user = await db.get(User, task.user_id)
            from app.graph.turn import run_turn

            await run_turn(session_id, task.user_id, user.name if user else "there", task.prompt)
            async with SessionLocal() as db:
                row = await db.get(ScheduledTask, task.id)
                if row is not None:
                    row.last_run_at = datetime.now(UTC)
                    await db.commit()
            log.info("scheduler.task_ran", task_id=str(task.id))
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler.task_failed", task_id=str(task.id), error=str(exc))


async def scheduler_loop() -> None:
    """Minute tick; cancelled cleanly on shutdown."""
    log.info("scheduler.started", briefing_time=settings.BRIEFING_TIME_IST)
    while True:
        try:
            now = _ist_now()
            await _run_briefings(now)
            await _run_alerts(now)
            await _run_tasks(now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log.error("scheduler.tick_failed", error=str(exc))
        await asyncio.sleep(TICK_SECONDS)
