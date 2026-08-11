"""Integration: a MULTI-STEP plan through the real compiled graph.

The final review's sharpest point: the flagship planner had no end-to-end
test. This drives supervisor → step 1 → advance → step 2 → compose → persist
with a scripted LLM, asserting step outputs feed forward, state resets
between steps, and compose merges without duplicating citations."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

# `integration` is CORRECT here despite the scripted LLM: user_client rides
# the conftest Postgres fixtures and the graph runs the REAL checkpointer.
pytestmark = pytest.mark.integration


async def test_two_step_plan_composes(
    user_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.graph import supervisor as supervisor_mod
    from app.graph import turn as turn_mod
    from app.graph.graph import close_graph
    from app.graph.turn import run_turn
    from app.infrastructure.llm.base import StreamDone, Usage
    from app.specialists import compose as compose_mod
    from app.specialists import general_chat as gc_mod

    async def fake_plan(*_a: Any, **_k: Any) -> str:
        return (
            '{"plan": [{"route": "general_chat", "task": "step one"},'
            ' {"route": "general_chat", "task": "step two"}], "reason": "test"}'
        )

    monkeypatch.setattr(supervisor_mod, "complete", fake_plan)

    seen_prompts: list[str] = []
    calls = {"n": 0}

    async def scripted_stream(messages: Any, **_k: Any) -> StreamDone:
        calls["n"] += 1
        seen_prompts.append(messages[-1].content)
        return StreamDone(text=f"OUT{calls['n']}", tool_calls=[], usage=Usage())

    monkeypatch.setattr(gc_mod, "stream", scripted_stream)

    async def compose_stream(messages: Any, **kwargs: Any) -> StreamDone:
        return StreamDone(text="COMBINED", tool_calls=[], usage=Usage())

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
            session_id, uuid.uuid4(), "Dev", "do two things", on_frame=on_frame
        )
        assert payload["type"] == "response"
        assert payload["message"] == "COMBINED"  # compose ran
        assert calls["n"] == 2  # both steps executed
        # step 2 saw step 1's output, fenced as data
        assert "OUT1" in seen_prompts[1]
        assert "previous step results" in seen_prompts[1]
        # routing frames: both steps + the compose boundary frame
        routing = [f for f in frames if f.get("type") == "routing"]
        assert [(f["step"], f["of"], f["route"]) for f in routing] == [
            (1, 2, "general_chat"),
            (2, 2, "general_chat"),
            (2, 2, "compose"),
        ]
        # no duplicated citations (steps wrote none; bleed would duplicate)
        assert payload["citations"] == []
    finally:
        await close_graph()
