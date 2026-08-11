"""Market-data tool family: quote, overview, symbol search, history.

Handlers return compact human-readable strings (the LLM narrates them);
results are Redis-cached at this call-site layer, keeping providers clean.
"""

import json
from dataclasses import asdict
from typing import Any

from app.infrastructure.llm.base import ToolDefinition
from app.market_data.cache import (
    HISTORY_TTL_S,
    OVERVIEW_TTL_S,
    QUOTE_TTL_S,
    SEARCH_TTL_S,
    cached_json,
)
from app.market_data.yfinance_provider import provider
from app.tools_registry.dispatcher import register_tool


async def _get_quote(args: dict[str, Any]) -> str:
    symbol = str(args.get("symbol", "")).strip()
    if not symbol:
        return "Error: 'symbol' is required."
    data = await cached_json(
        f"quote:{symbol.upper()}",
        QUOTE_TTL_S,
        lambda: _quote_dict(symbol),
    )
    return json.dumps(data)


async def _quote_dict(symbol: str) -> dict[str, Any]:
    return asdict(await provider.get_quote(symbol))


async def _get_overview(args: dict[str, Any]) -> str:
    symbol = str(args.get("symbol", "")).strip()
    if not symbol:
        return "Error: 'symbol' is required."
    data = await cached_json(
        f"overview:{symbol.upper()}",
        OVERVIEW_TTL_S,
        lambda: _overview_dict(symbol),
    )
    return json.dumps(data)


async def _overview_dict(symbol: str) -> dict[str, Any]:
    return asdict(await provider.get_company_overview(symbol))


async def _search_symbol(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' is required."
    data = await cached_json(
        f"search:{query.lower()}",
        SEARCH_TTL_S,
        lambda: _search_list(query),
    )
    return json.dumps(data)


async def _search_list(query: str) -> list[dict[str, Any]]:
    return [asdict(m) for m in await provider.search_symbol(query)]


async def _get_history(args: dict[str, Any]) -> str:
    symbol = str(args.get("symbol", "")).strip()
    period = str(args.get("period", "6mo"))
    if not symbol:
        return "Error: 'symbol' is required."
    points = await cached_json(
        f"hist:{symbol.upper()}:{period}",
        HISTORY_TTL_S,
        lambda: _history_list(symbol, period),
    )
    if not points:
        return (
            f"Error: no price history found for '{symbol}' over '{period}' — "
            "check the symbol (try search_symbol) or use a different period."
        )
    # summarize to first/last/min/max so the LLM isn't flooded with rows
    closes = [p["close"] for p in points]
    summary = {
        "symbol": symbol.upper(),
        "period": period,
        "points": len(points),
        "start": points[0],
        "end": points[-1],
        "min_close": min(closes),
        "max_close": max(closes),
        "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes[0] else None,
    }
    return json.dumps(summary)


async def _history_list(symbol: str, period: str) -> list[dict[str, Any]]:
    return [asdict(p) for p in await provider.get_historical_prices(symbol, period=period)]


_SYMBOL_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {
            "type": "string",
            "description": "NSE/BSE ticker or company name, e.g. 'TCS' or 'Reliance'",
        }
    },
    "required": ["symbol"],
}

register_tool(
    ToolDefinition(
        name="get_quote",
        description="Latest price, day change % and volume for an Indian (NSE/BSE) stock.",
        input_schema=_SYMBOL_SCHEMA,
    ),
    _get_quote,
)
register_tool(
    ToolDefinition(
        name="get_company_overview",
        description=(
            "Company fundamentals for an Indian stock: sector, market cap, P/E, P/B, "
            "dividend yield, EPS, ROE, 52-week range, business summary."
        ),
        input_schema=_SYMBOL_SCHEMA,
    ),
    _get_overview,
)
register_tool(
    ToolDefinition(
        name="search_symbol",
        description="Resolve a company name (possibly misspelled) to NSE/BSE ticker(s).",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Company name or ticker"}},
            "required": ["query"],
        },
    ),
    _search_symbol,
)
register_tool(
    ToolDefinition(
        name="get_historical_prices",
        description=(
            "Summarized OHLC price history for an Indian stock over a period "
            "(1mo/3mo/6mo/1y/5y): start/end/min/max close and % change."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE/BSE ticker or company name"},
                "period": {
                    "type": "string",
                    "enum": ["1mo", "3mo", "6mo", "1y", "5y"],
                    "description": "Lookback window (default 6mo)",
                },
            },
            "required": ["symbol"],
        },
    ),
    _get_history,
)
