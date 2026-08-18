"""CRAG knowledge correction (app/rag/crag.py) — grading, refinement, fallback.

Unit tests at the module seam (LLM + web search monkeypatched), plus one
rag_node-level test proving CRAG_ENABLED=false leaves today's path untouched.
The invariant under test throughout: correction may only ever be an upgrade —
every failure mode must degrade to keep-all-chunks (pre-CRAG behavior).
"""

import json
from typing import Any

import pytest

from app.rag import crag
from app.rag.crag import Block, CragResult, corrective_search, grade_blocks

pytestmark = pytest.mark.sanity

BLOCKS: list[Block] = [
    ("doc.pdf · p1", "The budget is 42 lakh rupees."),
    ("doc.pdf · p2", "Unrelated boilerplate about office hours."),
    ("glossary", "P/E ratio is price divided by earnings."),
]


def _grader_returning(payload: Any) -> Any:
    async def fake_complete(*_a: Any, **_kw: Any) -> str:
        return payload if isinstance(payload, str) else json.dumps(payload)

    return fake_complete


async def test_incorrect_chunks_dropped_correct_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        crag,
        "complete",
        _grader_returning(
            {"verdicts": ["correct", "incorrect", "ambiguous"], "web_query": "budget"}
        ),
    )
    result = await grade_blocks("what is the budget?", BLOCKS)
    assert result.grader_ok
    assert result.kept == [BLOCKS[0], BLOCKS[2]]  # incorrect one dropped
    assert result.dropped == 1
    assert result.overall == "ambiguous"  # mixed verdicts
    assert result.web_query == "budget"


async def test_all_correct_gives_overall_correct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        crag,
        "complete",
        _grader_returning({"verdicts": ["correct", "correct", "correct"]}),
    )
    result = await grade_blocks("q", BLOCKS)
    assert result.overall == "correct"
    assert result.kept == BLOCKS
    assert result.dropped == 0


async def test_all_incorrect_gives_overall_incorrect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        crag,
        "complete",
        _grader_returning({"verdicts": ["incorrect", "incorrect", "incorrect"]}),
    )
    result = await grade_blocks("q", BLOCKS)
    assert result.overall == "incorrect"
    assert result.kept == []


async def test_malformed_json_keeps_all_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(crag, "complete", _grader_returning("not json at all"))
    result = await grade_blocks("q", BLOCKS)
    assert not result.grader_ok
    assert result.kept == BLOCKS  # never-block: degrade to today's behavior


async def test_grader_exception_keeps_all_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a: Any, **_kw: Any) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(crag, "complete", boom)
    result = await grade_blocks("q", BLOCKS)
    assert not result.grader_ok
    assert result.kept == BLOCKS


async def test_unknown_and_missing_verdicts_default_to_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # one bogus value + one missing entry: both must degrade to "ambiguous"
    monkeypatch.setattr(crag, "complete", _grader_returning({"verdicts": ["banana", "correct"]}))
    result = await grade_blocks("q", BLOCKS)
    assert result.kept == BLOCKS  # nothing dropped
    assert result.overall == "ambiguous"


async def test_corrective_search_wraps_results_as_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.web_search import SearchResult

    async def fake_search(_q: str, max_results: int = 6) -> Any:
        return [SearchResult("TCS results", "https://x.test/a", "TCS Q3 profit rose")], (
            "duckduckgo"
        )

    monkeypatch.setattr(crag, "web_search", fake_search)
    blocks = await corrective_search("tcs q3 results")
    assert blocks == [("web · TCS results", "TCS Q3 profit rose (https://x.test/a)")]


async def test_corrective_search_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("network down")

    monkeypatch.setattr(crag, "web_search", boom)
    assert await corrective_search("q") == []


async def test_rag_node_skips_grading_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRAG_ENABLED=false must be byte-identical to the pre-CRAG path:
    grading never invoked, all retrieved blocks reach the prompt."""
    from app.core.config import settings as app_settings
    from app.infrastructure.llm.base import StreamDone, Usage
    from app.infrastructure.vector_store import Hit
    from app.specialists import rag as rag_mod

    monkeypatch.setattr(app_settings, "CRAG_ENABLED", False)

    async def fake_embed(_q: str) -> list[float]:
        return [0.0]

    async def fake_search(*_a: Any, **_kw: Any) -> list[Hit]:
        return [Hit(score=0.9, text="chunk", title="t", locator="p1", tenant="glossary")]

    async def fail_grade(*_a: Any, **_kw: Any) -> CragResult:
        raise AssertionError("grade_blocks must not run when CRAG is disabled")

    captured_system: list[str] = []

    async def fake_stream(messages: Any, **_kw: Any) -> StreamDone:
        captured_system.append(messages[0].content)
        return StreamDone(text="answer [1]", tool_calls=[], usage=Usage())

    monkeypatch.setattr(rag_mod, "embed_query", fake_embed)
    monkeypatch.setattr(rag_mod, "search", fake_search)
    monkeypatch.setattr(rag_mod, "grade_blocks", fail_grade)
    monkeypatch.setattr(rag_mod, "stream", fake_stream)

    result = await rag_mod.rag_node({"user_msg": "what is a P/E?", "user_id": "debug"})
    assert result["route"] == "rag"
    assert result["final_text"] == "answer [1]"
    assert "chunk" in captured_system[0]  # retrieved block reached the prompt
