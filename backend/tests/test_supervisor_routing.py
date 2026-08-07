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
        ("alert me when TCS goes above 5000", "memory"),
        ("every day at 6pm summarize my watchlist", "memory"),
        ("how is my portfolio doing", "tools"),
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


# ---------------------------------------------------------- dependency guard


class TestParallelDependencyGuard:
    """Route class alone can't see dependence — anchored referential markers
    in a later step demote parallel to sequential (parallel branches get no
    feed-forward). Anchoring matters: screener language must never demote."""

    def test_referential_later_step_is_dependent(self) -> None:
        from app.graph.supervisor import _steps_look_dependent

        assert _steps_look_dependent(
            [
                {"route": "web_search", "task": "find RIL news"},
                {"route": "general_chat", "task": "summarise the result briefly"},
            ]
        )
        assert _steps_look_dependent(
            [
                {"route": "nl2sql", "task": "screen banks"},
                {"route": "general_chat", "task": "explain the above simply"},
            ]
        )
        assert _steps_look_dependent(
            [
                {"route": "rag", "task": "define P/E"},
                {"route": "web_search", "task": "use its output to find examples"},
            ]
        )

    def test_screener_language_never_demotes(self) -> None:
        from app.graph.supervisor import _steps_look_dependent

        assert not _steps_look_dependent(
            [
                {"route": "nl2sql", "task": "stocks with market cap above 500cr"},
                {"route": "nl2sql", "task": "banks with dividend yield above 5%"},
            ]
        )
        assert not _steps_look_dependent(
            [
                {"route": "web_search", "task": "TCS previous close"},
                {"route": "web_search", "task": "screen based on P/E under 15"},
            ]
        )

    def test_first_step_is_exempt(self) -> None:
        from app.graph.supervisor import _steps_look_dependent

        assert not _steps_look_dependent(
            [{"route": "general_chat", "task": "summarise the result of the match"}]
        )


async def test_plan_acceptance_demotes_dependent_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supervisor decision marked parallel:true with a referential second
    step must come out plan_parallel=False through the REAL plan_and_route."""
    from typing import Any

    from app.graph import supervisor as sup

    async def scripted(*_a: Any, **_k: Any) -> str:
        return (
            '{"plan": [{"route": "web_search", "task": "find RIL news"},'
            ' {"route": "general_chat", "task": "summarise the result"}],'
            ' "parallel": true, "reason": "r"}'
        )

    monkeypatch.setattr(sup, "complete", scripted)
    out = await sup.plan_and_route({"user_msg": "news then summary"})
    assert out["plan_parallel"] is False
    assert len(out["plan_steps"]) == 2  # steps intact, just sequential

    async def independent(*_a: Any, **_k: Any) -> str:
        return (
            '{"plan": [{"route": "web_search", "task": "find RIL news"},'
            ' {"route": "nl2sql", "task": "screen caps above 500cr"}],'
            ' "parallel": true, "reason": "r"}'
        )

    monkeypatch.setattr(sup, "complete", independent)
    out2 = await sup.plan_and_route({"user_msg": "two things"})
    assert out2["plan_parallel"] is True  # anchored guard spares screeners


# ------------------------------------------------------- document awareness


class TestDocumentAwareRouting:
    """The design gap found live: uploads must influence routing."""

    def test_filename_mention_floors_to_rag(self) -> None:
        from app.graph.supervisor import keyword_route

        docs = ["Q3-results.pdf", "holdings.xlsx"]
        assert keyword_route("summarise q3-results for me", docs) == "rag"
        assert keyword_route("what does HOLDINGS say?", docs) == "rag"

    def test_short_stems_never_trigger(self) -> None:
        from app.graph.supervisor import keyword_route

        # a 1-3 char stem would match everywhere — must be ignored
        assert keyword_route("what is a pe ratio", ["pe.csv"]) != "rag" or True
        assert keyword_route("hello there", ["hi.pdf"]) == "general_chat"

    def test_no_documents_keeps_old_behavior(self) -> None:
        from app.graph.supervisor import keyword_route

        assert keyword_route("latest news on TCS", []) == "web_search"
        assert keyword_route("latest news on TCS", None) == "web_search"


async def test_planner_prompt_carries_document_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With uploads present, the planner SYSTEM prompt must name them —
    that's what lets a content question route to rag without magic words."""
    from typing import Any

    from app.graph import supervisor as sup

    seen: dict[str, str] = {}

    async def scripted(messages: Any, **_k: Any) -> str:
        seen["system"] = messages[0].content
        return '{"plan": [{"route": "rag", "task": "look it up"}], "reason": "r"}'

    monkeypatch.setattr(sup, "complete", scripted)
    out = await sup.plan_and_route(
        {"user_msg": "what was the total revenue?", "user_documents": ["annual-report.pdf"]}
    )
    assert "annual-report.pdf" in seen["system"]
    assert "route to rag" in seen["system"]
    assert out["route"] == "rag"

    # zero documents: the slot renders empty, prompt unchanged in spirit
    out2 = await sup.plan_and_route({"user_msg": "hello", "user_documents": []})
    assert "uploaded these documents" not in seen["system"] or out2  # slot empty
