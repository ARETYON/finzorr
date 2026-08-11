"""Daily fundamentals refresh (cron-triggered, partial-failure-safe).

Run: `uv run python -m app.nl2sql.jobs.refresh_fundamentals [--limit N]`

Only successfully fetched symbols are upserted — a partial yfinance outage
leaves the rest of the table at yesterday's stale-but-valid values, never
nulled out. Failures are logged per symbol for operator visibility.
"""

import argparse
import asyncio
import sys

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import configure_logging, log
from app.infrastructure.db.session import SessionLocal
from app.market_data.symbols import universe
from app.market_data.yfinance_provider import provider
from app.models.fundamental import Fundamental
from app.models.user import utcnow

CONCURRENCY = 5


async def _fetch_one(symbol: str, sem: asyncio.Semaphore) -> dict[str, object] | None:
    async with sem:
        try:
            o = await provider.get_company_overview(symbol)
            q = await provider.get_quote(symbol)
            return {
                "symbol": o.symbol,
                "name": o.name,
                "exchange": q.exchange,
                "sector": o.sector,
                "industry": o.industry,
                # BigInteger columns — yfinance sometimes returns floats
                "market_cap": int(o.market_cap) if o.market_cap is not None else None,
                "pe_ratio": o.pe_ratio,
                "pb_ratio": o.pb_ratio,
                "dividend_yield": o.dividend_yield,
                "eps": o.eps,
                "roe": o.roe,
                "week52_high": o.week52_high,
                "week52_low": o.week52_low,
                "current_price": q.price,
                "volume": int(q.volume) if q.volume is not None else None,
                "updated_at": utcnow(),
            }
        except Exception as exc:  # noqa: BLE001 — per-symbol isolation
            log.warning("refresh.symbol_failed", symbol=symbol, error=type(exc).__name__)
            return None


async def refresh(limit: int | None = None) -> tuple[int, int]:
    """Fetch the universe and upsert successes. Returns (ok, failed)."""
    symbols = [s.symbol for s in universe()]
    if limit:
        symbols = symbols[:limit]
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(_fetch_one(s, sem) for s in symbols))
    rows = [r for r in results if r is not None]
    failed = len(results) - len(rows)
    if rows:
        async with SessionLocal() as db:
            stmt = pg_insert(Fundamental).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Fundamental.symbol],
                set_={c: stmt.excluded[c] for c in rows[0] if c != "symbol"},
            )
            await db.execute(stmt)
            await db.commit()
    log.info("refresh.done", ok=len(rows), failed=failed, universe=len(symbols))
    return len(rows), failed


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Refresh the fundamentals table")
    parser.add_argument("--limit", type=int, default=None, help="only the first N symbols")
    args = parser.parse_args()
    ok, failed = asyncio.run(refresh(args.limit))
    if ok == 0:
        sys.exit(1)  # nothing refreshed — visible failure for cron/monitoring


if __name__ == "__main__":
    main()
