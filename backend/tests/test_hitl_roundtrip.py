"""Integration: the full HITL interrupt roundtrip against a REAL checkpointer.

A sensitive tool call must park the turn (approval_required), survive the
park durably, then resume with Command(resume=...) — approval executes the
tool, decline substitutes the honest refusal. The LLM is faked; the graph,
checkpointer, and interrupt machinery are real.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.fixture
async def _fake_llm_and_tool(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A sensitive test tool + a scripted 'LLM' that calls it once."""
    from app.ai.base import StreamDone, ToolCallRequest, ToolDefinition, Usage
    from app.core.config import settings
    from app.graph.nodes import tools as tools_mod
    from app.tools_registry.dispatcher import register_tool

    executions: list[dict[str, Any]] = []

    async def fake_sensitive(args: dict[str, Any]) -> str:
        executions.append(args)
        return "SENSITIVE RAN OK"

    register_tool(
        ToolDefinition(
            name="fake_sensitive",
            description="test",
            input_schema={"type": "object", "properties": {}},
        ),
        fake_sensitive,
    )
    monkeypatch.setattr(settings, "HITL_TOOLS", "fake_sensitive")

    calls = {"n": 0}

    async def scripted_stream(*_a: Any, **_k: Any) -> StreamDone:
        calls["n"] += 1
        if calls["n"] == 1:
            return StreamDone(
                text="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="fake_sensitive", arguments_json="{}")
                ],
                usage=Usage(),
            )
        return StreamDone(text="done after tool", tool_calls=[], usage=Usage())

    monkeypatch.setattr(tools_mod, "stream", scripted_stream)

    # supervisor + memory: no live LLM/Qdrant in tests
    async def fake_complete(*_a: Any, **_k: Any) -> str:
        return '{"plan": [{"route": "tools", "task": "run it"}], "reason": "test"}'

    from app.graph import supervisor as supervisor_mod
    from app.graph import turn as turn_mod

    monkeypatch.setattr(supervisor_mod, "complete", fake_complete)

    async def no_recall(*_a: Any, **_k: Any) -> list[str]:
        return []

    async def no_instructions(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr("app.memory.facts.recall", no_recall)
    monkeypatch.setattr("app.memory.facts.extract_and_store", no_recall)
    monkeypatch.setattr(turn_mod, "_load_instructions", no_instructions)
    return executions


async def test_interrupt_park_and_approve(
    user_client: AsyncClient, _fake_llm_and_tool: list[dict[str, Any]]
) -> None:
    from app.graph.graph import close_graph
    from app.graph.turn import resume_turn, run_turn

    executions = _fake_llm_and_tool
    session_id = uuid.UUID((await user_client.post("/api/chat/sessions", json={})).json()["id"])
    try:
        parked = await run_turn(session_id, uuid.uuid4(), "Dev", "run the sensitive thing")
        assert parked["type"] == "approval_required"
        assert parked["tools"][0]["name"] == "fake_sensitive"
        assert executions == []  # NOTHING ran before approval

        resumed = await resume_turn(session_id, approved=True)
        assert resumed["type"] == "response"
        assert resumed["message"] == "done after tool"
        assert len(executions) == 1  # approval executed it exactly once
    finally:
        await close_graph()


async def test_interrupt_park_and_decline(
    user_client: AsyncClient, _fake_llm_and_tool: list[dict[str, Any]]
) -> None:
    from app.graph.graph import close_graph
    from app.graph.turn import resume_turn, run_turn

    executions = _fake_llm_and_tool
    session_id = uuid.UUID((await user_client.post("/api/chat/sessions", json={})).json()["id"])
    try:
        parked = await run_turn(session_id, uuid.uuid4(), "Dev", "run the sensitive thing")
        assert parked["type"] == "approval_required"

        resumed = await resume_turn(session_id, approved=False)
        assert resumed["type"] == "response"
        assert executions == []  # decline: the tool NEVER ran
    finally:
        await close_graph()
