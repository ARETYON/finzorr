"""Sanity: yfinance provider (mocked yfinance) + market-data cache edges."""

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from app.market_data.cache import _serialize, cached_json
from app.market_data.yfinance_provider import (
    SymbolNotFoundError,
    YFinanceProvider,
    _fetch_info,
    _resolve_yahoo_symbol,
)

pytestmark = pytest.mark.sanity


# ---------------------------------------------------------------- symbol resolution


def test_resolve_known_ticker_gets_ns_suffix() -> None:
    assert _resolve_yahoo_symbol("TCS") == "TCS.NS"


def test_resolve_keeps_explicit_suffix() -> None:
    assert _resolve_yahoo_symbol("TCS.NS") == "TCS.NS"
    assert _resolve_yahoo_symbol("tcs.bo") == "TCS.BO"


def test_resolve_exact_ticker_any_case_or_padding() -> None:
    assert _resolve_yahoo_symbol("  infy  ") == "INFY.NS"
    assert _resolve_yahoo_symbol("tcs") == "TCS.NS"


def test_resolve_multiword_name_resolves_to_ticker() -> None:
    # Was a KNOWN GAP (case-sensitive fuzzy match tanked multi-word names
    # below the 70 floor after the caller uppercased); fixed by scoring
    # ticker/name separately with rapidfuzz's default_process.
    assert _resolve_yahoo_symbol("reliance industries") == "RELIANCE.NS"
    assert _resolve_yahoo_symbol("RELIANCE INDUSTRIES") == "RELIANCE.NS"


def test_resolve_unknown_falls_back_to_ns_probe() -> None:
    assert _resolve_yahoo_symbol("zzzzqqqq") == "ZZZZQQQQ.NS"


# ---------------------------------------------------------------- mocked yfinance


class _FakeTicker:
    def __init__(self, info: dict[str, Any], frame: "_FakeFrame | None" = None) -> None:
        self.info = info
        self._frame = frame or _FakeFrame([])

    def history(self, period: str = "6mo", interval: str = "1d") -> "_FakeFrame":
        return self._frame


class _FakeRow:
    def __init__(self, values: dict[str, float]) -> None:
        self._values = values

    def __getitem__(self, key: str) -> float:
        return self._values[key]


@dataclass
class _FakeIndex:
    day: date

    def date(self) -> date:
        return self.day


class _FakeFrame:
    def __init__(self, rows: list[tuple[date, dict[str, float]]]) -> None:
        self._rows = rows

    def iterrows(self) -> list[tuple[_FakeIndex, _FakeRow]]:
        return [(_FakeIndex(day), _FakeRow(values)) for day, values in self._rows]


class _FakeYF:
    def __init__(self, ticker: _FakeTicker) -> None:
        self._ticker = ticker
        self.requested: list[str] = []

    def Ticker(self, symbol: str) -> _FakeTicker:  # noqa: N802 — mimics yfinance API
        self.requested.append(symbol)
        return self._ticker


def _use_yf(monkeypatch: pytest.MonkeyPatch, ticker: _FakeTicker) -> _FakeYF:
    fake = _FakeYF(ticker)
    monkeypatch.setattr("app.market_data.yfinance_provider.yf", fake)
    return fake


def test_fetch_info_raises_when_no_price(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_yf(monkeypatch, _FakeTicker({"shortName": "Ghost Corp"}))
    with pytest.raises(SymbolNotFoundError):
        _fetch_info("GHOST.NS")


def test_fetch_info_accepts_current_price_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_yf(monkeypatch, _FakeTicker({"currentPrice": 12.5}))
    assert _fetch_info("X.NS")["currentPrice"] == 12.5


async def test_get_quote_computes_day_change(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_yf(
        monkeypatch,
        _FakeTicker(
            {
                "regularMarketPrice": 110.0,
                "regularMarketPreviousClose": 100.0,
                "shortName": "Tata Consultancy",
                "currency": "INR",
                "regularMarketVolume": 1234,
            }
        ),
    )
    quote = await YFinanceProvider().get_quote("TCS")
    assert quote.symbol == "TCS"
    assert quote.exchange == "NSE"
    assert quote.price == 110.0
    assert quote.day_change_pct == 10.0
    assert quote.volume == 1234
    assert quote.name == "Tata Consultancy"


async def test_get_quote_without_previous_close_has_no_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_yf(monkeypatch, _FakeTicker({"regularMarketPrice": 50.0}))
    quote = await YFinanceProvider().get_quote("TCS")
    assert quote.day_change_pct is None
    assert quote.currency == "INR"  # default when yfinance omits it


async def test_get_quote_bse_suffix_maps_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_yf(monkeypatch, _FakeTicker({"regularMarketPrice": 5.0}))
    quote = await YFinanceProvider().get_quote("SOMETHING.BO")
    assert quote.exchange == "BSE"
    assert quote.symbol == "SOMETHING"


async def test_overview_truncates_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_yf(
        monkeypatch,
        _FakeTicker(
            {
                "regularMarketPrice": 10.0,
                "longName": "Tata Consultancy Services",
                "sector": "IT",
                "trailingPE": 30.5,
                "longBusinessSummary": "x" * 600,
            }
        ),
    )
    overview = await YFinanceProvider().get_company_overview("TCS")
    assert overview.name == "Tata Consultancy Services"
    assert overview.sector == "IT"
    assert overview.pe_ratio == 30.5
    assert overview.summary == "x" * 500


async def test_overview_non_string_summary_becomes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_yf(
        monkeypatch,
        _FakeTicker({"regularMarketPrice": 10.0, "longBusinessSummary": 12345}),
    )
    overview = await YFinanceProvider().get_company_overview("TCS")
    assert overview.summary is None
    assert overview.market_cap is None


async def test_history_maps_rows_to_price_points(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _FakeFrame(
        [
            (
                date(2026, 8, 3),
                {"Open": 10.111, "High": 11.129, "Low": 9.5, "Close": 10.9, "Volume": 500.0},
            )
        ]
    )
    _use_yf(monkeypatch, _FakeTicker({"regularMarketPrice": 10.0}, frame))
    points = await YFinanceProvider().get_historical_prices("TCS")
    assert len(points) == 1
    point = points[0]
    assert point.date == "2026-08-03"
    assert point.open == 10.11 and point.high == 11.13  # rounded to 2dp
    assert point.volume == 500 and isinstance(point.volume, int)


async def test_history_empty_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_yf(monkeypatch, _FakeTicker({"regularMarketPrice": 10.0}, _FakeFrame([])))
    with pytest.raises(SymbolNotFoundError):
        await YFinanceProvider().get_historical_prices("TCS")


async def test_search_symbol_uses_seed_index() -> None:
    matches = await YFinanceProvider().search_symbol("infosys")
    assert matches and matches[0].symbol == "INFY"


# ---------------------------------------------------------------- cache


@dataclass
class _Sample:
    symbol: str
    price: float


class _FakeCacheRedis:
    def __init__(self, fail_writes: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail_writes = fail_writes

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        if self.fail_writes:
            raise RuntimeError("write refused")
        self.store[key] = value
        return True


def test_serialize_dataclass_and_lists() -> None:
    assert _serialize(_Sample("TCS", 1.5)) == '{"symbol": "TCS", "price": 1.5}'
    assert _serialize([_Sample("A", 1.0), _Sample("B", 2.0)]) == (
        '[{"symbol": "A", "price": 1.0}, {"symbol": "B", "price": 2.0}]'
    )
    assert _serialize([1, 2, 3]) == "[1, 2, 3]"
    assert _serialize({"plain": True}) == '{"plain": true}'


async def test_cached_json_fetches_once_then_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCacheRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    calls = 0

    async def fetch() -> _Sample:
        nonlocal calls
        calls += 1
        return _Sample("TCS", 99.0)

    first = await cached_json("q:tcs", 45, fetch)
    second = await cached_json("q:tcs", 45, fetch)
    assert calls == 1
    assert first == _Sample("TCS", 99.0)  # miss returns the fetched value as-is
    assert second == {"symbol": "TCS", "price": 99.0}  # hit returns the cached JSON


async def test_cached_json_fails_open_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> _FakeCacheRedis:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)
    calls = 0

    async def fetch() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"n": calls}

    assert await cached_json("k", 45, fetch) == {"n": 1}
    assert await cached_json("k", 45, fetch) == {"n": 2}  # no cache: every call fetches


async def test_cached_json_swallows_write_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCacheRedis(fail_writes=True)
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

    async def fetch() -> str:
        return "value"

    assert await cached_json("k", 45, fetch) == "value"
    assert fake.store == {}
