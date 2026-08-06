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

# `integration` is CORRECT here despite the scripted LLM: user_client rides
# the conftest Postgres fixtures and the graph runs the REAL checkpointer.
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


async def test_stale_approval_errors_instead_of_replaying(
    user_client: AsyncClient, _fake_llm_and_tool: list[dict[str, Any]]
) -> None:
    """An approval frame with NO parked turn must error — langgraph happily
    resumes a thread with no pending interrupt to its last state, which
    would replay the previous answer."""
    from app.graph.graph import close_graph
    from app.graph.turn import resume_turn

    session_id = uuid.UUID((await user_client.post("/api/chat/sessions", json={})).json()["id"])
    try:
        resumed = await resume_turn(session_id, approved=True)
        assert resumed["type"] == "error"
        assert "nothing to approve" in resumed["message"]
        assert _fake_llm_and_tool == []  # and nothing executed
    finally:
        await close_graph()


async def test_new_message_abandons_parked_turn(
    user_client: AsyncClient, _fake_llm_and_tool: list[dict[str, Any]]
) -> None:
    """A new message on a thread with a parked interrupt abandons it cleanly:
    the parked exchange lands in the DB transcript, the pending approval
    disappears, and the sensitive tool never runs."""
    from app.graph.graph import close_graph
    from app.graph.turn import get_parked_approval, run_turn

    executions = _fake_llm_and_tool
    session_id = uuid.UUID((await user_client.post("/api/chat/sessions", json={})).json()["id"])
    try:
        parked = await run_turn(session_id, uuid.uuid4(), "Dev", "run the sensitive thing")
        assert parked["type"] == "approval_required"

        second = await run_turn(session_id, uuid.uuid4(), "Dev", "actually never mind")
        assert second["type"] in ("response", "approval_required")

        assert executions == []  # the abandoned tool call never executed
        contents = [
            m["content"]
            for m in (
                await user_client.get(f"/api/chat/sessions/{session_id}/messages")
            ).json()
        ]
        assert "run the sensitive thing" in contents  # parked msg not lost
        assert any("superseded" in c for c in contents)
        if second["type"] == "response":
            assert await get_parked_approval(session_id) is None
    finally:
        await close_graph()


async def test_pending_approval_endpoint_is_typed(
    user_client: AsyncClient, _fake_llm_and_tool: list[dict[str, Any]]
) -> None:
    """The rediscovery endpoint returns typed tools ({name}) — v1 and alias."""
    from app.graph.graph import close_graph
    from app.graph.turn import run_turn

    session_id = uuid.UUID((await user_client.post("/api/chat/sessions", json={})).json()["id"])
    try:
        before = await user_client.get(f"/api/v1/chat/sessions/{session_id}/pending-approval")
        assert before.json() == {"pending": False, "tools": []}

        parked = await run_turn(session_id, uuid.uuid4(), "Dev", "run the sensitive thing")
        assert parked["type"] == "approval_required"

        after = await user_client.get(f"/api/v1/chat/sessions/{session_id}/pending-approval")
        assert after.status_code == 200
        body = after.json()
        assert body["pending"] is True
        assert body["tools"] == [{"name": "fake_sensitive", "arguments": {}}]
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
