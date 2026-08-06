"""Deterministic coverage for surfaces the wave shipped faster than tests:
the replan node (scripted LLM), the memory node's action machinery (real
DB), and the feedback / search / persona / share REST paths.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.db.session import SessionLocal
from app.graph.nodes.memory import _apply_actions, _get_alerts, _get_watchlist, parse_reply
from app.graph.nodes.replan import replan_node
from app.graph.state import AssistantState
from app.models.message import Message

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------- replan node


def _failed_plan_state() -> AssistantState:
    return {
        "user_msg": "compare TCS and Infosys",
        "plan_steps": [
            {"route": "web_search", "task": "find TCS news"},
            {"route": "nl2sql", "task": "screen fundamentals"},
        ],
        "plan_index": 1,  # advance moved past the failed step
        "step_outputs": [
            {"route": "web_search", "task": "find TCS news", "output": "SEARCH FAILED"}
        ],
        "needs_replan": True,
        "step_error": True,
        "citations": [{"marker": "[1]", "snippet": "stale"}],
    }


async def test_replan_revises_remaining_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai.completion as completion_mod

    async def scripted(*_a: Any, **_k: Any) -> str:
        return '{"plan": [{"route": "general_chat", "task": "answer directly"}], "reason": "r"}'

    monkeypatch.setattr(completion_mod, "complete", scripted)
    out = await replan_node(_failed_plan_state())
    assert out["needs_replan"] is False
    assert out["replan_count"] == 1
    assert out["step_error"] is False
    assert out["route"] == "general_chat"
    assert out["current_task"] == "answer directly"
    # completed prefix kept, remainder replaced
    assert out["plan_steps"] == [
        {"route": "web_search", "task": "find TCS news"},
        {"route": "general_chat", "task": "answer directly"},
    ]
    assert out["plan_index"] == 1
    # clean-slate discipline when arming the revised step
    assert out["citations"] == []
    assert out["final_text"] == ""
    assert out["pending_tool_calls"] == []


async def test_replan_empty_revision_early_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai.completion as completion_mod

    async def scripted(*_a: Any, **_k: Any) -> str:
        return '{"plan": [], "reason": "nothing sensible remains"}'

    monkeypatch.setattr(completion_mod, "complete", scripted)
    out = await replan_node(_failed_plan_state())
    assert out["plan_index"] == 2  # == len(plan_steps): straight to compose
    assert out["needs_replan"] is False
    assert out["replan_count"] == 1


async def test_replan_llm_failure_early_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai.completion as completion_mod

    async def broken(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(completion_mod, "complete", broken)
    out = await replan_node(_failed_plan_state())
    assert out["plan_index"] == 2
    assert out["replan_count"] == 1  # the one revision budget is spent either way


async def test_final_step_failure_still_replans() -> None:
    """A failure on the LAST (or only) step gets the one replan attempt too —
    that's exactly where a revision can still rescue the answer."""
    from app.graph.nodes.advance import advance_node

    out = await advance_node(
        {
            "user_msg": "q",
            "plan_steps": [{"route": "rag", "task": "look it up"}],
            "plan_index": 0,
            "step_outputs": [],
            "step_error": True,
            "replan_count": 0,
            "final_text": "I couldn't search the knowledge base right now.",
        }
    )
    assert out["needs_replan"] is True
    assert out["step_error"] is False


async def test_final_step_failure_after_replan_ends_normally() -> None:
    from app.graph.nodes.advance import advance_node

    out = await advance_node(
        {
            "user_msg": "q",
            "plan_steps": [{"route": "rag", "task": "t"}, {"route": "general_chat", "task": "t2"}],
            "plan_index": 1,  # last step
            "step_outputs": [{"route": "rag", "task": "t", "output": "x"}],
            "step_error": True,
            "replan_count": 1,  # budget spent
            "final_text": "degraded",
        }
    )
    assert out.get("needs_replan", False) is False
    assert out["step_error"] is False
    assert out["plan_index"] == 2  # plan over; compose surfaces the failure


async def test_rag_and_memory_degrades_set_step_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every specialist failure path must raise the replanner's only signal."""
    from app.graph.nodes import memory as memory_mod
    from app.graph.nodes import rag as rag_mod

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("backend down")

    # retrieval failure alone degrades to an ungrounded-but-honest answer;
    # the step-failure signal fires when the synthesis LLM call dies too
    monkeypatch.setattr(rag_mod, "embed_query", boom)
    monkeypatch.setattr(rag_mod, "stream", boom)
    rag_out = await rag_mod.rag_node({"user_msg": "q", "user_id": ""})
    assert rag_out["step_error"] is True

    monkeypatch.setattr(memory_mod, "stream", boom)
    mem_out = await memory_mod.memory_node(
        {"user_msg": "q", "user_id": "", "user_name": "Dev"}
    )
    assert mem_out["step_error"] is True


async def test_research_synthesis_refuses_with_zero_sources() -> None:
    """No sources gathered => refuse + flag, never a fabricated 'report'."""
    from app.graph.nodes.research import research_synthesize_node

    out = await research_synthesize_node(
        {"user_msg": "q", "current_task": "", "research_sources": [], "research_pages": []}
    )
    assert out["step_error"] is True
    assert "couldn't gather any sources" in out["final_text"]
    assert out["citations"] == []


# ---------------------------------------------------------------- memory node


class TestParseReply:
    def test_clean_json(self) -> None:
        assert parse_reply('{"message": "hi", "actions": []}')["message"] == "hi"

    def test_json_inside_prose(self) -> None:
        parsed = parse_reply('Sure! {"message": "added", "actions": []} Done.')
        assert parsed["message"] == "added"

    def test_plain_text_degrades(self) -> None:
        assert parse_reply("just text") == {"message": "just text", "actions": []}

    def test_empty_degrades_to_done(self) -> None:
        assert parse_reply("   ") == {"message": "Done.", "actions": []}


async def _dev_user_id(user_client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await user_client.get("/api/auth/me")).json()["id"])


async def test_apply_actions_idempotent_and_isolated(user_client: AsyncClient) -> None:
    user_id = await _dev_user_id(user_client)
    actions: list[dict[str, Any]] = [
        {"type": "watchlist_add", "symbol": "tcs"},
        {"type": "watchlist_add", "symbol": "TCS"},  # duplicate: no conflict
        {"type": "watchlist_add", "symbol": ""},  # invalid: skipped
        {"type": "alert_create", "symbol": "INFY", "direction": "above", "target": 2000},
        {"type": "alert_create", "symbol": "INFY", "direction": "sideways", "target": 1},
        {"type": "task_create", "spec": "daily@09:00", "prompt": "morning brief"},
        {"type": "task_create", "spec": "hourly@x", "prompt": "nope"},  # bad spec
    ]
    applied = await _apply_actions(user_id, actions)
    assert [a["type"] for a in applied] == [
        "watchlist_add",
        "watchlist_add",
        "alert_create",
        "task_create",
    ]
    assert await _get_watchlist(user_id) == ["TCS"]  # uppercased, deduped
    assert await _get_alerts(user_id) == ["INFY above 2000.0"]

    await _apply_actions(user_id, [{"type": "watchlist_remove", "symbol": "TCS"}])
    assert await _get_watchlist(user_id) == []


# ---------------------------------------------------------------- REST paths


async def _create_session(client: AsyncClient) -> str:
    response = await client.post("/api/chat/sessions", json={})
    assert response.status_code == 201
    return str(response.json()["id"])


async def _seed_exchange(session_id: str) -> str:
    """A user+assistant exchange directly in the DB; returns assistant id."""
    async with SessionLocal() as db:
        user_msg = Message(
            session_id=uuid.UUID(session_id), role="user", content="what moved NIFTY today?"
        )
        db.add(user_msg)
        await db.flush()
        reply = Message(
            session_id=uuid.UUID(session_id),
            role="assistant",
            content="Banks led the NIFTY higher.",
            route="general_chat",
        )
        db.add(reply)
        await db.commit()
        return str(reply.id)


async def test_rename_session_and_ownership(
    user_client: AsyncClient, other_client: AsyncClient
) -> None:
    session_id = await _create_session(user_client)
    renamed = await user_client.patch(
        f"/api/chat/sessions/{session_id}", json={"title": "My analysis"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "My analysis"

    other = await other_client.patch(
        f"/api/chat/sessions/{session_id}", json={"title": "hijack"}
    )
    assert other.status_code == 404


async def test_feedback_roundtrip_captures_query(user_client: AsyncClient) -> None:
    session_id = await _create_session(user_client)
    message_id = await _seed_exchange(session_id)
    created = await user_client.post(
        f"/api/chat/messages/{message_id}/feedback",
        json={"rating": 1, "comment": "spot on"},
    )
    assert created.status_code == 201
    assert created.json()["id"]

    missing = await user_client.post(
        f"/api/chat/messages/{uuid.uuid4()}/feedback", json={"rating": -1}
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


async def test_search_returns_wrapped_snippets(user_client: AsyncClient) -> None:
    session_id = await _create_session(user_client)
    await _seed_exchange(session_id)
    hits = (await user_client.get("/api/chat/search", params={"q": "NIFTY"})).json()
    assert len(hits) == 2
    assert all(h["session_id"] == session_id for h in hits)
    blank = (await user_client.get("/api/chat/search", params={"q": "  "})).json()
    assert blank == []


async def test_persona_crud_and_session_binding(user_client: AsyncClient) -> None:
    created = await user_client.post(
        "/api/personas", json={"name": "Tutor", "system_prompt": "explain simply"}
    )
    assert created.status_code == 201
    persona_id = created.json()["id"]
    listed = (await user_client.get("/api/personas")).json()
    assert [p["name"] for p in listed] == ["Tutor"]

    session_id = await _create_session(user_client)
    bound = await user_client.patch(
        f"/api/chat/sessions/{session_id}/persona", json={"persona_id": persona_id}
    )
    assert bound.status_code == 200

    assert (await user_client.delete(f"/api/personas/{persona_id}")).status_code == 204
    assert (await user_client.get("/api/personas")).json() == []


async def test_share_view_revoke_and_expiry(
    user_client: AsyncClient, client: AsyncClient
) -> None:
    session_id = await _create_session(user_client)
    await _seed_exchange(session_id)

    token = (await user_client.post(f"/api/chat/sessions/{session_id}/share")).json()["token"]
    shared = await client.get(f"/api/share/{token}")  # public: NO auth
    assert shared.status_code == 200
    assert [m["role"] for m in shared.json()["messages"]] == ["user", "assistant"]

    assert (
        await user_client.delete(f"/api/chat/sessions/{session_id}/share")
    ).status_code == 204
    dead = await client.get(f"/api/share/{token}")
    assert dead.status_code == 404
    assert dead.json()["code"] == "not_found"

    # a second revoke has nothing to delete
    assert (
        await user_client.delete(f"/api/chat/sessions/{session_id}/share")
    ).status_code == 404
