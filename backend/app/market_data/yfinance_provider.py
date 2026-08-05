"""Yahoo Finance implementation (free/unofficial, `.NS`/`.BO` suffixes).

yfinance is synchronous — every call is wrapped in `asyncio.to_thread` so it
never blocks the event loop (and every concurrent WS chat with it).
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from app.market_data import symbols as symbol_index
from app.market_data.base import (
    CompanyOverview,
    MarketDataProvider,
    PricePoint,
    QuoteData,
    SymbolMatch,
)

_SUFFIXES = (".NS", ".BO")


class SymbolNotFoundError(Exception):
    """Raised when no ticker variant yields data."""


def _resolve_yahoo_symbol(symbol: str) -> str:
    """Map a bare NSE/BSE ticker to Yahoo's suffixed form."""
    upper = symbol.strip().upper()
    if upper.endswith(_SUFFIXES):
        return upper
    matches = symbol_index.search(upper, limit=1)
    if matches:
        suffix = ".NS" if matches[0].exchange == "NSE" else ".BO"
        return f"{matches[0].symbol}{suffix}"
    return f"{upper}.NS"


def _fetch_info(yahoo_symbol: str) -> dict[str, Any]:
    info: dict[str, Any] = yf.Ticker(yahoo_symbol).info or {}
    if not info.get("regularMarketPrice") and not info.get("currentPrice"):
        raise SymbolNotFoundError(yahoo_symbol)
    return info


class YFinanceProvider(MarketDataProvider):
    """Free-tier provider; all methods raise SymbolNotFoundError on misses."""

    async def get_quote(self, symbol: str) -> QuoteData:
        ysym = _resolve_yahoo_symbol(symbol)
        info = await asyncio.to_thread(_fetch_info, ysym)
        price = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
        prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
        change = round((price - prev) / prev * 100, 2) if prev else None
        return QuoteData(
            symbol=ysym.split(".")[0],
            name=str(info.get("shortName") or info.get("longName") or ysym),
            exchange="NSE" if ysym.endswith(".NS") else "BSE",
            price=price,
            currency=str(info.get("currency") or "INR"),
            day_change_pct=change,
            volume=info.get("regularMarketVolume"),
            as_of=datetime.now(UTC).isoformat(),
        )

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        ysym = _resolve_yahoo_symbol(symbol)
        info = await asyncio.to_thread(_fetch_info, ysym)
        summary = info.get("longBusinessSummary")
        return CompanyOverview(
            symbol=ysym.split(".")[0],
            name=str(info.get("shortName") or info.get("longName") or ysym),
            sector=info.get("sector"),
            industry=info.get("industry"),
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            pb_ratio=info.get("priceToBook"),
            dividend_yield=info.get("dividendYield"),
            eps=info.get("trailingEps"),
            roe=info.get("returnOnEquity"),
            week52_high=info.get("fiftyTwoWeekHigh"),
            week52_low=info.get("fiftyTwoWeekLow"),
            summary=summary[:500] if isinstance(summary, str) else None,
        )

    async def search_symbol(self, query: str) -> list[SymbolMatch]:
        return symbol_index.search(query)

    async def get_historical_prices(
        self, symbol: str, period: str = "6mo", interval: str = "1d"
    ) -> list[PricePoint]:
        ysym = _resolve_yahoo_symbol(symbol)

        def _fetch() -> list[PricePoint]:
            df = yf.Ticker(ysym).history(period=period, interval=interval)
            return [
                PricePoint(
                    date=str(idx.date()),
                    open=round(float(row["Open"]), 2),
                    high=round(float(row["High"]), 2),
                    low=round(float(row["Low"]), 2),
                    close=round(float(row["Close"]), 2),
                    volume=int(row["Volume"]),
                )
                for idx, row in df.iterrows()
            ]

        points = await asyncio.to_thread(_fetch)
        if not points:
            raise SymbolNotFoundError(ysym)
        return points


provider: MarketDataProvider = YFinanceProvider()
