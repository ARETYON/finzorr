"""Sanity: symbol resolution + dispatcher contract — no network, no live deps."""

import pytest

from app.market_data.symbols import load_symbols, search, universe
from app.tools_registry import market_tools  # noqa: F401 — registers tools
from app.tools_registry.dispatcher import all_tools, dispatch

pytestmark = pytest.mark.sanity


def test_seed_universe_loads() -> None:
    symbols = load_symbols()
    assert len(symbols) >= 50
    assert all(s.exchange in {"NSE", "BSE"} for s in symbols)


def test_exact_ticker_match_wins() -> None:
    matches = search("TCS")
    assert matches and matches[0].symbol == "TCS" and matches[0].score == 100.0


def test_fuzzy_company_name_resolution() -> None:
    assert search("Reliance")[0].symbol == "RELIANCE"
    assert search("Infosys")[0].symbol == "INFY"
    assert search("tata consultancy")[0].symbol == "TCS"


def test_fuzzy_match_is_case_insensitive_for_multiword_names() -> None:
    """X11 regression: callers uppercase before searching; multi-word names
    must still clear the score floor (matching was case-sensitive), and a
    'Ltd'-suffixed query must not lose its bank to a longer-named one."""
    assert search("RELIANCE INDUSTRIES")[0].symbol == "RELIANCE"
    assert search("TATA CONSULTANCY SERVICES")[0].symbol == "TCS"
    assert search("HDFC BANK LTD")[0].symbol == "HDFCBANK"


def test_nonsense_query_returns_empty() -> None:
    assert search("zzzzqqqq totally unknown co") == []


def test_universe_matches_seed() -> None:
    assert len(universe()) == len(load_symbols())


def test_market_tools_registered() -> None:
    names = {t.name for t in all_tools()}
    assert {"get_quote", "get_company_overview", "search_symbol", "get_historical_prices"} <= names


async def test_dispatcher_never_raises_on_unknown_tool() -> None:
    result = await dispatch("does_not_exist", {})
    assert result.startswith("Error:")


async def test_dispatcher_never_raises_on_bad_args() -> None:
    # missing required arg -> handler returns an error string, never an exception
    result = await dispatch("get_quote", {})
    assert result.startswith("Error:")
