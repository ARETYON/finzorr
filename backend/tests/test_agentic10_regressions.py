"""Regression tests for the agentic-10 wave's properties.

Covers: plan validation and the advance/after_step state machine, compose
citation merging, the dispatcher argument validator, HITL degraded-mode
auto-decline, the turn lock fallback, checkpointer reattach scheduling, and
idempotent persist markers. All deterministic.
"""

from typing import Any

import pytest

pytestmark = pytest.mark.sanity


# ------------------------------------------------------------ plan validation

class TestValidatePlan:
    def test_single_and_multi_step(self) -> None:
        from app.orchestration.supervisor import validate_plan

        steps = validate_plan(
            [
                {"route": "web_search", "task": "find news"},
                {"route": "tools", "task": "get the price"},
            ],
            "orig",
        )
        assert [s["route"] for s in steps] == ["web_search", "tools"]

    def test_unknown_routes_dropped_and_capped(self) -> None:
        from app.orchestration.supervisor import validate_plan

        steps = validate_plan(
            [
                {"route": "nonsense", "task": "x"},
                {"route": "tools", "task": ""},
                {"route": "rag", "task": "b"},
                {"route": "memory", "task": "c"},
                {"route": "nl2sql", "task": "d"},
            ],
            "orig",
        )
        # cap is applied to the RAW list (3 entries considered), invalid dropped
        assert len(steps) <= 3
        assert all(s["route"] in {"tools", "rag", "memory", "nl2sql"} for s in steps)
        assert all(s["task"] for s in steps)  # empty task fell back to user_msg

    def test_garbage_yields_empty(self) -> None:
        from app.orchestration.supervisor import validate_plan

        assert validate_plan("not a list", "m") == []
        assert validate_plan(["just strings"], "m") == []
        assert validate_plan(None, "m") == []


# ------------------------------------------------- advance / after_step

class TestPlanStateMachine:
    async def test_advance_arms_next_step(self) -> None:
        from app.specialists.advance import advance_node

        state: dict[str, Any] = {
            "plan_steps": [
                {"route": "web_search", "task": "find news"},
                {"route": "tools", "task": "get price"},
            ],
            "plan_index": 0,
            "step_outputs": [],
            "final_text": "the news",
            "citations": [{"marker": "[1]"}],
            "route": "web_search",
            "session_id": "s",
        }
        out = await advance_node(state)  # type: ignore[arg-type]
        assert out["plan_index"] == 1
        assert out["route"] == "tools"
        assert out["current_task"] == "get price"
        assert out["step_outputs"][0]["output"] == "the news"
        assert out["tool_iterations"] == 0  # fresh tool loop per step

    async def test_advance_final_step_records_only(self) -> None:
        from app.specialists.advance import advance_node

        state: dict[str, Any] = {
            "plan_steps": [{"route": "tools", "task": "t"}],
            "plan_index": 0,
            "step_outputs": [],
            "final_text": "answer",
            "route": "tools",
            "session_id": "s",
        }
        out = await advance_node(state)  # type: ignore[arg-type]
        assert out["plan_index"] == 1
        assert "route" not in out  # nothing more to arm

    def test_after_step_routing(self) -> None:
        from app.orchestration.state import AssistantState
        from app.specialists.advance import after_step

        steps = [{"route": "web_search", "task": "a"}, {"route": "tools", "task": "b"}]
        mid: AssistantState = {"plan_steps": steps, "plan_index": 1, "step_outputs": [{}]}
        done: AssistantState = {"plan_steps": steps, "plan_index": 2, "step_outputs": [{}, {}]}
        single: AssistantState = {
            "plan_steps": [{"route": "rag", "task": "a"}],
            "plan_index": 1,
            "step_outputs": [{}],
        }
        assert after_step(mid) == "tools"
        assert after_step(done) == "compose"
        assert after_step(single) == "persist"


# ------------------------------------------------------------- compose

class TestCompose:
    async def test_merges_citations_and_degrades_without_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.specialists import compose

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise ConnectionError("no llm in tests")

        monkeypatch.setattr(compose, "stream", boom)
        out = await compose.compose_node(
            {
                "user_msg": "x",
                "step_outputs": [
                    {"route": "web_search", "task": "a", "output": "one", "citations": [{"m": 1}]},
                    {"route": "tools", "task": "b", "output": "two", "citations": [{"m": 2}]},
                ],
            }
        )
        assert "one" in out["final_text"] and "two" in out["final_text"]
        # citations survive with globally renumbered markers
        assert [c["marker"] for c in out["citations"]] == ["[1]", "[2]"]
        assert out["sources"] == []
        assert out["chart"] == {}


# ------------------------------------------------- dispatcher arg validation

class TestArgumentValidation:
    def _definition(self) -> Any:
        from app.infrastructure.llm.base import ToolDefinition

        return ToolDefinition(
            name="t",
            description="d",
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["symbol"],
            },
        )

    def test_missing_required(self) -> None:
        from app.tools_registry.dispatcher import validate_arguments

        assert validate_arguments(self._definition(), {}) is not None
        assert validate_arguments(self._definition(), {"symbol": ""}) is not None

    def test_wrong_types(self) -> None:
        from app.tools_registry.dispatcher import validate_arguments

        assert validate_arguments(self._definition(), {"symbol": 42}) is not None
        assert (
            validate_arguments(self._definition(), {"symbol": "TCS", "count": "3"}) is not None
        )
        # bool must not satisfy integer
        assert (
            validate_arguments(self._definition(), {"symbol": "TCS", "count": True}) is not None
        )

    def test_valid_and_extra_args_pass(self) -> None:
        from app.tools_registry.dispatcher import validate_arguments

        assert validate_arguments(self._definition(), {"symbol": "TCS", "count": 3}) is None
        assert validate_arguments(self._definition(), {"symbol": "TCS", "extra": 1}) is None


# ------------------------------------------------------------- HITL degraded

class TestHitlDegraded:
    async def test_sensitive_tools_auto_decline_without_checkpointer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import settings
        from app.specialists import tools as tools_mod

        monkeypatch.setattr(settings, "HITL_TOOLS", "run_python")
        out = await tools_mod.tools_exec_node(
            {
                "user_id": "u",
                "hitl_enabled": False,
                "pending_tool_calls": [
                    {"id": "c1", "name": "run_python", "arguments": {"code": "print(1)"}}
                ],
                "tool_transcript": [],
            }
        )
        transcript = out["tool_transcript"]
        assert len(transcript) == 1
        assert "approval" in transcript[0]["content"]
        assert out["pending_tool_calls"] == []


# --------------------------------------------------------------- turn lock

class TestTurnLock:
    async def test_claim_release_cycle_local_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.infrastructure import turn_lock

        def no_redis() -> Any:
            raise ConnectionError("redis down")

        monkeypatch.setattr("app.infrastructure.redis.get_redis", no_redis)
        turn_lock._local.clear()
        assert await turn_lock.claim("s1") is True
        assert await turn_lock.claim("s1") is False  # local set still guards
        await turn_lock.release("s1")
        assert await turn_lock.claim("s1") is True
        await turn_lock.release("s1")


# ------------------------------------------------- checkpointer reattach

class TestDegradedReattach:
    def test_retry_window_is_scheduled(self) -> None:
        """Structural: degraded compiles set a retry deadline instead of
        freezing forever, and the fast path honors it."""
        import inspect

        from app.orchestration import graph as graph_mod

        source = inspect.getsource(graph_mod.get_graph)
        assert "_degraded_retry_at" in source
        assert "_DEGRADED_RETRY_S" in inspect.getsource(graph_mod)


# ------------------------------------------------------- idempotent persist

class TestIdempotentPersist:
    async def test_marker_set_after_commit_not_before(self) -> None:
        """Structural: the persisted-marker must be written AFTER the commit
        — claim-before-write would suppress the RetryPolicy's second attempt."""
        import inspect

        from app.specialists import persist

        source = inspect.getsource(persist.persist_node)
        assert source.index("_already_persisted") < source.index("_mark_persisted")
        assert source.index("await db.commit()") < source.index("_mark_persisted")
