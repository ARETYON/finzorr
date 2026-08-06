"""Integration: forced replan through the REAL compiled graph.

The 9.0 review's remaining gap: replan was tested at node level only. This
drives supervisor → step 1 (web_search, scripted to FAIL with step_error) →
advance → replan (scripted revision to general_chat) → revised specialist →
compose → persist, asserting the conditional-edge seam, the revised routing
frame, and the reducer resets the node tests can't see.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

# `integration` is CORRECT despite the scripted LLM: user_client rides the
# conftest Postgres fixtures and the graph runs the REAL checkpointer.
pytestmark = pytest.mark.integration


async def test_failed_step_replans_through_compiled_graph(
    user_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.base import StreamDone, Usage
    from app.graph import supervisor as supervisor_mod
    from app.graph import turn as turn_mod
    from app.graph.graph import close_graph
    from app.graph.nodes import compose as compose_mod
    from app.graph.nodes import general_chat as gc_mod
    from app.graph.nodes import replan as replan_mod
    from app.graph.nodes import web_search as ws_mod
    from app.graph.turn import run_turn

    async def fake_plan(*_a: Any, **_k: Any) -> str:
        return (
            '{"plan": [{"route": "web_search", "task": "find the news"},'
            ' {"route": "nl2sql", "task": "screen numbers"}], "reason": "test"}'
        )

    monkeypatch.setattr(supervisor_mod, "complete", fake_plan)

    # step 1: every search provider fails -> the node's degrade path sets
    # step_error (core/web_search swallows provider errors into an empty list)
    async def no_results(*_a: Any, **_k: Any) -> Any:
        return [], "none"

    monkeypatch.setattr(ws_mod, "search", no_results)

    # replan LLM: revise the remainder to a single general_chat step
    async def fake_revision(*_a: Any, **_k: Any) -> str:
        return (
            '{"plan": [{"route": "general_chat", "task": "answer from knowledge"}],'
            ' "reason": "search is down"}'
        )

    monkeypatch.setattr(replan_mod, "complete", fake_revision, raising=False)
    import app.ai.completion as completion_mod

    monkeypatch.setattr(completion_mod, "complete", fake_revision)

    gc_calls = {"n": 0}

    async def scripted_gc(*_a: Any, **_k: Any) -> StreamDone:
        gc_calls["n"] += 1
        return StreamDone(text="RESCUED", tool_calls=[], usage=Usage())

    monkeypatch.setattr(gc_mod, "stream", scripted_gc)

    async def compose_stream(*_a: Any, **_k: Any) -> StreamDone:
        return StreamDone(text="FINAL WITH RESCUE", tool_calls=[], usage=Usage())

    monkeypatch.setattr(compose_mod, "stream", compose_stream)

    async def nothing(*_a: Any, **_k: Any) -> Any:
        return []

    async def empty(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr("app.memory.facts.recall", nothing)
    monkeypatch.setattr("app.memory.facts.extract_and_store", nothing)
    monkeypatch.setattr(turn_mod, "_load_instructions", empty)

    session_id = uuid.UUID((await user_client.post("/api/chat/sessions", json={})).json()["id"])
    frames: list[dict[str, Any]] = []

    async def on_frame(frame: dict[str, Any]) -> None:
        frames.append(frame)

    try:
        payload = await run_turn(
            session_id, uuid.uuid4(), "Dev", "news then numbers", on_frame=on_frame
        )
        assert payload["type"] == "response"
        assert payload["message"] == "FINAL WITH RESCUE"  # compose ran over both
        assert gc_calls["n"] == 1  # the REVISED specialist executed
        routing = [(f["step"], f["of"], f["route"]) for f in frames if f.get("type") == "routing"]
        # step 1 armed by supervisor; replan emits the revised step's frame;
        # compose closes. nl2sql (the abandoned original step 2) never runs.
        assert routing[0] == (1, 2, "web_search")
        assert any(r == (2, 2, "general_chat") for r in routing)
        assert routing[-1][2] == "compose"
        assert all(r[2] != "nl2sql" for r in routing)
        # the failed step is surfaced in the record, not silently dropped
        reasons = [str(f.get("reason", "")) for f in frames if f.get("type") == "routing"]
        assert any("replanned" in r for r in reasons), "replan must emit its routing frame"
    finally:
        await close_graph()
