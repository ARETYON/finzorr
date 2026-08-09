"""trace.py helpers + the traced() degraded-tag guard (M3).

The guard wraps ALL 17 graph nodes — a bug here is a global blast radius,
so: GraphInterrupt must pass through untouched (HITL depends on it),
step_error returns must tag, and internal tagging failures must vanish.
"""

from typing import Any

import pytest

pytestmark = pytest.mark.sanity


def test_mark_and_tag_noop_without_tracing() -> None:
    from app.core.trace import mark, tag

    # tracing is pinned off in tests — these must be silent no-ops
    mark(anything=1, other="x")
    tag("a", "b")


def test_helpers_swallow_internal_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import trace as trace_mod

    def boom() -> Any:
        raise RuntimeError("tracer exploded")

    monkeypatch.setattr(trace_mod, "get_current_run_tree", boom)
    trace_mod.mark(x=1)  # must not raise
    trace_mod.tag("t")  # must not raise


async def test_traced_passes_graph_interrupt_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph.errors import GraphInterrupt

    from app.graph.graph import traced

    async def interrupting(_state: Any) -> Any:
        raise GraphInterrupt(())

    wrapped = traced("tools_exec", interrupting)
    with pytest.raises(GraphInterrupt):
        await wrapped({})


async def test_traced_tags_degraded_on_step_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import trace as trace_mod
    from app.graph.graph import traced

    tagged: list[str] = []
    monkeypatch.setattr(trace_mod, "tag", lambda *t: tagged.extend(t))

    async def failing(_state: Any) -> Any:
        return {"final_text": "degraded prose", "step_error": True}

    out = await traced("web_search", failing)({})
    assert out["step_error"] is True  # output untouched
    assert tagged == ["degraded", "degraded:web_search"]

    tagged.clear()

    async def healthy(_state: Any) -> Any:
        return {"final_text": "fine"}

    await traced("web_search", healthy)({})
    assert tagged == []  # clean nodes stay untagged


async def test_traced_tagging_failure_never_breaks_the_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import trace as trace_mod
    from app.graph.graph import traced

    def exploding_tag(*_t: str) -> None:
        raise RuntimeError("tracer down")

    # trace.tag itself swallows errors; simulate a pathological failure by
    # patching the symbol traced() imports — the node result must survive
    monkeypatch.setattr(trace_mod, "get_current_run_tree", exploding_tag)

    async def failing(_state: Any) -> Any:
        return {"step_error": True}

    out = await traced("rag", failing)({})
    assert out == {"step_error": True}


# --------------------------------------------------- drift watch compare (M5)


def test_drift_compare_flags_regressions_and_broken_evals() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from drift_watch import compare

    yesterday = {"routing": 0.94, "injection": 1.0, "plan": 1.0}
    today_ok = {"routing": 0.94, "injection": 1.0, "plan": 1.0}
    assert compare(yesterday, today_ok) == []

    today_regressed = {"routing": 0.90, "injection": 1.0, "plan": -1.0}
    alerts = compare(yesterday, today_regressed)
    assert any("routing" in a and "94.00%" in a for a in alerts)
    assert any("plan" in a and "failed to run" in a for a in alerts)

    # improvement is not an alert; first run (no yesterday) is quiet
    assert compare({}, {"routing": 0.96}) == []
