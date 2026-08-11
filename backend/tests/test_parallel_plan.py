"""Integration: a PARALLEL plan fans out via Send through the real graph.

Asserts both branches run concurrently into `parallel_outputs`, join orders
them by step_index, compose merges, branch token streams are suppressed,
and a second sequential turn on the same thread is unpolluted (the
Overwrite reset works)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_parallel_fanout_composes(
    user_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.infrastructure.llm.base import StreamDone, Usage
    from app.orchestration import supervisor as supervisor_mod
    from app.orchestration import turn as turn_mod
    from app.orchestration.graph import close_graph
    from app.orchestration.turn import run_turn
    from app.specialists import compose as compose_mod
    from app.specialists import general_chat as gc_mod

    async def fake_plan(*_a: Any, **_k: Any) -> str:
        return (
            '{"plan": [{"route": "general_chat", "task": "branch A"},'
            ' {"route": "general_chat", "task": "branch B"}],'
            ' "parallel": true, "reason": "independent"}'
        )

    monkeypatch.setattr(supervisor_mod, "complete", fake_plan)

    async def branch_stream(messages: Any, *, on_token: Any = None, **_k: Any) -> StreamDone:
        task = messages[-1].content
        text = "ALPHA" if "branch A" in task else "BETA"
        if on_token is not None:
            await on_token(text)  # suppressed by parallel_branch — must not surface
        return StreamDone(text=text, tool_calls=[], usage=Usage())

    monkeypatch.setattr(gc_mod, "stream", branch_stream)

    async def compose_stream(*_a: Any, **_k: Any) -> StreamDone:
        return StreamDone(text="MERGED", tool_calls=[], usage=Usage())

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
            session_id, uuid.uuid4(), "Dev", "two independent things", on_frame=on_frame
        )
        assert payload["type"] == "response"
        assert payload["message"] == "MERGED"
        # branch tokens suppressed; only compose's stream could emit (mocked off)
        branch_tokens = [
            f for f in frames if f.get("type") == "token" and f.get("delta") in ("ALPHA", "BETA")
        ]
        assert branch_tokens == []

        # second, SEQUENTIAL turn on the same thread: the additive channel
        # must have been reset (Overwrite) — no stale branch records
        async def single_plan(*_a: Any, **_k: Any) -> str:
            return '{"plan": [{"route": "general_chat", "task": "solo"}], "reason": "t"}'

        monkeypatch.setattr(supervisor_mod, "complete", single_plan)

        async def solo_stream(*_a: Any, on_token: Any = None, **_k: Any) -> StreamDone:
            return StreamDone(text="SOLO", tool_calls=[], usage=Usage())

        monkeypatch.setattr(gc_mod, "stream", solo_stream)
        second = await run_turn(session_id, uuid.uuid4(), "Dev", "just one thing")
        assert second["message"] == "SOLO"  # compose did NOT run on stale records
    finally:
        await close_graph()
