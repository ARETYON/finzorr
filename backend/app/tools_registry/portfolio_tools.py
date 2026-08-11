"""Portfolio analysis: latest uploaded holdings CSV × live quotes -> P&L.

Reads the current user's most recent .csv document (columns matched flexibly:
symbol/ticker, qty/quantity/shares, avg_price/buy_price/price), joins live
quotes, and returns totals, per-holding P&L, and allocation percentages.
"""

import asyncio
import csv
import io
import uuid
from typing import Any

from sqlalchemy import select

from app.core.logging import log
from app.core.request_context import get_current_user_id
from app.documents.storage import get_storage
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.llm.base import ToolDefinition
from app.models.document import Document
from app.tools_registry.dispatcher import register_tool

_SYMBOL_KEYS = ("symbol", "ticker", "stock")
_QTY_KEYS = ("qty", "quantity", "shares", "units")
_PRICE_KEYS = ("avg_price", "buy_price", "price", "avg cost", "avgcost", "average price")


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    lowered = {k.lower().strip(): v for k, v in row.items() if k}
    for key in keys:
        if key in lowered:
            return lowered[key]
    return None


def _parse_holdings(text: str) -> list[dict[str, float | str]]:
    holdings: list[dict[str, float | str]] = []
    for row in csv.DictReader(io.StringIO(text)):
        symbol = _pick(row, _SYMBOL_KEYS)
        qty_raw, price_raw = _pick(row, _QTY_KEYS), _pick(row, _PRICE_KEYS)
        if not symbol or not qty_raw:
            continue
        try:
            holdings.append(
                {
                    "symbol": symbol.strip().upper(),
                    "qty": float(qty_raw),
                    "avg_price": float(price_raw) if price_raw else 0.0,
                }
            )
        except ValueError:
            continue
    return holdings


async def _latest_csv(user_id: uuid.UUID) -> tuple[str, str] | None:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
                Document.filename.ilike("%.csv"),
                Document.status == "ready",
            )
            .order_by(Document.uploaded_at.desc())
            .limit(1)
        )
        doc = result.scalars().first()
    if doc is None:
        return None
    data = await get_storage().load(doc.storage_key)
    return doc.filename, data.decode("utf-8", errors="replace")


async def _analyze(_args: dict[str, Any]) -> str:
    raw_user = get_current_user_id()
    try:
        user_id = uuid.UUID(raw_user)
    except ValueError:
        return "Error: no user context for portfolio analysis."
    latest = await _latest_csv(user_id)
    if latest is None:
        return (
            "No holdings CSV found. Ask the user to upload a CSV with columns like "
            "symbol, qty, avg_price via the Documents panel first."
        )
    filename, text = latest
    holdings = _parse_holdings(text)
    if not holdings:
        return f"Could not parse holdings from {filename} — expected symbol/qty/avg_price columns."

    from app.market_data.yfinance_provider import provider

    sem = asyncio.Semaphore(5)

    async def quote(symbol: str) -> float | None:
        async with sem:
            try:
                return (await provider.get_quote(symbol)).price
            except Exception:  # noqa: BLE001
                return None

    prices = await asyncio.gather(*(quote(str(h["symbol"])) for h in holdings))
    lines = [
        "| Symbol | Qty | Avg ₹ | Now ₹ | Value ₹ | P&L ₹ | P&L % |",
        "|---|---|---|---|---|---|---|",
    ]
    total_value = total_cost = 0.0
    priced = 0
    for holding, price in zip(holdings, prices, strict=True):
        qty, avg = float(holding["qty"]), float(holding["avg_price"])
        if price is None:
            lines.append(f"| {holding['symbol']} | {qty:g} | {avg:,.2f} | ? | ? | ? | ? |")
            continue
        priced += 1
        value, cost = qty * price, qty * avg
        total_value += value
        total_cost += cost
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost else 0.0
        lines.append(
            f"| {holding['symbol']} | {qty:g} | {avg:,.2f} | {price:,.2f} | "
            f"{value:,.0f} | {pnl:+,.0f} | {pnl_pct:+.1f}% |"
        )
    summary = (
        f"Portfolio from {filename} ({priced}/{len(holdings)} symbols priced): "
        f"current value ₹{total_value:,.0f}"
    )
    if total_cost:
        total_pnl = total_value - total_cost
        summary += (
            f", invested ₹{total_cost:,.0f}, P&L ₹{total_pnl:+,.0f} "
            f"({total_pnl / total_cost * 100:+.1f}%)"
        )
    log.info("portfolio.analyzed", holdings=len(holdings), priced=priced)
    return summary + "\n\n" + "\n".join(lines)


register_tool(
    ToolDefinition(
        name="analyze_portfolio",
        description=(
            "Analyze the user's uploaded holdings CSV (symbol, qty, avg_price): "
            "current value, per-holding P&L, and totals using live prices. Use when "
            "the user asks about their portfolio/holdings performance."
        ),
        input_schema={"type": "object", "properties": {}},
    ),
    _analyze,
)
