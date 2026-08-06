"""Company-name -> ticker resolution over the curated NSE/BSE seed list.

yfinance has no search endpoint, so we fuzzy-match (rapidfuzz) against a
committed CSV (manual quarterly refresh for MVP). Unknown inputs fall through
to direct `.NS`/`.BO` suffix probing in the provider.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, utils

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
    # Score ticker and company name SEPARATELY and keep the best: a single
    # "SYMBOL Name" string dilutes short names ("HDFC Bank" vs a "... Ltd"
    # query loses to longer-named banks). default_process lowercases/strips
    # both sides — without it, matching is case-sensitive and multi-word
    # names from uppercasing callers score below the 70 floor (both found
    # by the X11 review agent). 58 seed rows: a direct loop is fine.
    scored = [
        (
            max(
                fuzz.WRatio(query, s.symbol, processor=utils.default_process),
                fuzz.WRatio(query, s.name, processor=utils.default_process),
            ),
            i,
        )
        for i, s in enumerate(symbols)
    ]
    scored.sort(key=lambda pair: -pair[0])
    return [
        SymbolMatch(symbols[i].symbol, symbols[i].name, symbols[i].exchange, float(score))
        for score, i in scored[:limit]
        if score >= MIN_MATCH_SCORE
    ]


def universe() -> list[SeedSymbol]:
    """The full seed universe (used by the fundamentals refresh job)."""
    return list(load_symbols())
