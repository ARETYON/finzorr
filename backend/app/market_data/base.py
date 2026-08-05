"""MarketDataProvider ABC — the swap-in seam for paid vendors later.

The agent layer depends only on this interface; replacing yfinance with a
broker API (Kite/Upstox) is one new class here, zero changes elsewhere.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class QuoteData:
    symbol: str
    name: str
    exchange: str
    price: float
    currency: str
    day_change_pct: float | None
    volume: int | None
    as_of: str  # ISO timestamp of the fetch


@dataclass
class CompanyOverview:
    symbol: str
    name: str
    sector: str | None
    industry: str | None
    market_cap: float | None
    pe_ratio: float | None
    pb_ratio: float | None
    dividend_yield: float | None
    eps: float | None
    roe: float | None
    week52_high: float | None
    week52_low: float | None
    summary: str | None


@dataclass
class SymbolMatch:
    symbol: str
    name: str
    exchange: str
    score: float


@dataclass
class PricePoint:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketDataProvider(ABC):
    """Contract every market-data vendor implementation must satisfy."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> QuoteData: ...

    @abstractmethod
    async def get_company_overview(self, symbol: str) -> CompanyOverview: ...

    @abstractmethod
    async def search_symbol(self, query: str) -> list[SymbolMatch]: ...

    @abstractmethod
    async def get_historical_prices(
        self, symbol: str, period: str = "6mo", interval: str = "1d"
    ) -> list[PricePoint]: ...
