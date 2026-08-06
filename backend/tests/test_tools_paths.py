"""Portfolio-analysis and market tool handlers with a scripted provider —
the CSV parsing, P&L math, and error paths run for real; only yfinance
(network) is faked.
"""

import uuid
from dataclasses import dataclass

import pytest

from app.core.request_context import user_context
from app.tools_registry.portfolio_tools import _analyze, _parse_holdings

# ---------------------------------------------------------------- CSV parsing


class TestParseHoldings:
    def test_flexible_column_names(self) -> None:
        text = "Ticker,Shares,Buy_Price\ntcs,10,3500\ninfy,5,1500\n"
        holdings = _parse_holdings(text)
        assert holdings == [
            {"symbol": "TCS", "qty": 10.0, "avg_price": 3500.0},
            {"symbol": "INFY", "qty": 5.0, "avg_price": 1500.0},
        ]

    def test_bad_rows_skipped_price_optional(self) -> None:
        text = "symbol,qty\nTCS,ten\nINFY,5\n,3\n"
        assert _parse_holdings(text) == [{"symbol": "INFY", "qty": 5.0, "avg_price": 0.0}]

    def test_empty_csv(self) -> None:
        assert _parse_holdings("") == []


# ---------------------------------------------------------------- analyze


@dataclass
class _Quote:
    price: float


async def test_analyze_without_user_context() -> None:
    assert "no user context" in await _analyze({})


async def test_analyze_without_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools_registry import portfolio_tools

    async def no_csv(_uid: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(portfolio_tools, "_latest_csv", no_csv)
    with user_context(str(uuid.uuid4())):
        assert "No holdings CSV found" in await _analyze({})


async def test_analyze_joins_quotes_and_computes_pnl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.market_data import yfinance_provider
    from app.tools_registry import portfolio_tools

    async def fake_csv(_uid: uuid.UUID) -> tuple[str, str]:
        return "holdings.csv", "symbol,qty,avg_price\nTCS,10,3000\nZZZZ,2,100\n"

    async def fake_quote(symbol: str) -> _Quote:
        if symbol == "ZZZZ":
            raise RuntimeError("unknown")
        return _Quote(price=3300.0)

    monkeypatch.setattr(portfolio_tools, "_latest_csv", fake_csv)
    monkeypatch.setattr(yfinance_provider.provider, "get_quote", fake_quote)
    with user_context(str(uuid.uuid4())):
        report = await _analyze({})
    assert "1/2 symbols priced" in report
    assert "value ₹33,000" in report
    assert "+3,000" in report  # 10 × (3300 − 3000)
    assert "| ZZZZ | 2 | 100.00 | ? |" in report  # unpriced row degrades to ?

    unparseable = "symbol\nTCS\n"

    async def bad_csv(_uid: uuid.UUID) -> tuple[str, str]:
        return "bad.csv", unparseable

    monkeypatch.setattr(portfolio_tools, "_latest_csv", bad_csv)
    with user_context(str(uuid.uuid4())):
        assert "Could not parse holdings" in await _analyze({})


# ---------------------------------------------------------------- market tools


async def test_market_tool_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.market_data import yfinance_provider
    from app.market_data.base import QuoteData, SymbolMatch
    from app.tools_registry.market_tools import _get_quote, _search_symbol

    async def fake_quote(symbol: str) -> QuoteData:
        return QuoteData(
            symbol=symbol.upper(),
            name="Fake Co",
            exchange="NSE",
            price=100.5,
            currency="INR",
            day_change_pct=1.2,
            volume=10,
            as_of="2026-08-06T00:00:00+00:00",
        )

    async def fake_search(query: str) -> list[SymbolMatch]:
        return [SymbolMatch(symbol="TCS", name=query, exchange="NSE", score=99.0)]

    monkeypatch.setattr(yfinance_provider.provider, "get_quote", fake_quote)
    monkeypatch.setattr(yfinance_provider.provider, "search_symbol", fake_search)

    # unique symbols so a warm Redis can't serve a stale hit
    nonce = uuid.uuid4().hex[:8].upper()
    quoted = await _get_quote({"symbol": f"Q{nonce}"})
    assert f"Q{nonce}" in quoted and "100.5" in quoted
    assert "required" in await _get_quote({})

    searched = await _search_symbol({"query": f"co-{nonce}"})
    assert "TCS" in searched
    assert "required" in await _search_symbol({})
