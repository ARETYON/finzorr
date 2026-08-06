"""Sanity: deterministic keyword-fallback routing — no LLM, no DB, no network."""

import pytest

from app.graph.supervisor import ROUTES, keyword_route

pytestmark = pytest.mark.sanity


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # memory / watchlist
        ("add TCS to my watchlist", "memory"),
        ("what's on my watch list?", "memory"),
        # nl2sql / screening
        ("show stocks with PE under 15", "nl2sql"),
        ("top 10 stocks by market cap above 1000000", "nl2sql"),
        ("stocks having dividend yield above 3", "nl2sql"),
        # web search / news
        ("latest news about Reliance", "web_search"),
        ("why did Adani stock fall today", "web_search"),
        ("what happened in the markets this week", "web_search"),
        # tools / live market data — the classic price-vs-news disambiguation
        ("price of TCS", "tools"),
        ("what is Infosys trading at", "tools"),
        ("52-week high of HDFC Bank", "tools"),
        ("fundamentals of ITC", "tools"),
        # rag / concepts + documents
        ("what is P/E ratio", "rag"),
        ("explain circuit limits", "rag"),
        ("what does my contract say about notice period", "rag"),
        # URL pasted -> tools (read_url)
        ("summarize https://example.com/article", "tools"),
        ("what does this say http://news.site/x", "tools"),
        # default
        ("write me a poem about the sea", "general_chat"),
        ("hello!", "general_chat"),
    ],
)
def test_keyword_route(message: str, expected: str) -> None:
    assert keyword_route(message) == expected


def test_keyword_route_always_returns_valid_route() -> None:
    for message in ("", "asdf qwerty", "🙂", "SELECT * FROM x"):
        assert keyword_route(message) in ROUTES
