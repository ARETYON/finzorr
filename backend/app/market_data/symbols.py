"""Company-name -> ticker resolution over the curated NSE/BSE seed list.

yfinance has no search endpoint, so we fuzzy-match (rapidfuzz) against a
committed CSV (manual quarterly refresh for MVP). Unknown inputs fall through
to direct `.NS`/`.BO` suffix probing in the provider.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

from app.market_data.base import SymbolMatch

_CSV_PATH = Path(__file__).parent / "data" / "nse_bse_symbols.csv"
MIN_MATCH_SCORE = 70.0


@dataclass(frozen=True)
class SeedSymbol:
    symbol: str
    name: str
    exchange: str


@lru_cache
def load_symbols() -> tuple[SeedSymbol, ...]:
    """Read the committed seed list once per process."""
    with _CSV_PATH.open() as f:
        return tuple(
            SeedSymbol(row["symbol"], row["name"], row["exchange"]) for row in csv.DictReader(f)
        )


def search(query: str, limit: int = 5) -> list[SymbolMatch]:
    """Fuzzy-match a name or ticker against the seed list."""
    symbols = load_symbols()
    query_upper = query.strip().upper()
    # exact ticker hit wins outright
    for s in symbols:
        if s.symbol.upper() == query_upper:
            return [SymbolMatch(s.symbol, s.name, s.exchange, 100.0)]
    choices = {i: f"{s.symbol} {s.name}" for i, s in enumerate(symbols)}
    results = process.extract(query, choices, scorer=fuzz.WRatio, limit=limit)
    return [
        SymbolMatch(symbols[idx].symbol, symbols[idx].name, symbols[idx].exchange, float(score))
        for _choice, score, idx in results
        if score >= MIN_MATCH_SCORE
    ]


def universe() -> list[SeedSymbol]:
    """The full seed universe (used by the fundamentals refresh job)."""
    return list(load_symbols())
