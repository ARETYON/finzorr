"""Plan advancement: the state machine that makes the supervisor's plan real.

Every specialist edge lands here. The node records the finished step's
output, then either (a) arms the next step — new `current_task`, routing
frame, conditional edge to the next specialist — or (b) hands off to
`compose` (multi-step) / `persist` (the common single-step case). Because
this is a graph node, every completed step is its own checkpointed superstep:
a crash mid-plan resumes with earlier steps' outputs intact.
"""

from typing import Any

from app.core.logging import log
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame
from app.specialists.base import Specialist

STEP_RECORD_CHARS = 2000  # stored per step in state (checkpointed)


async def advance_node(state: AssistantState) -> AssistantState:
    """Record the completed step; arm the next one if the plan has more."""
    steps = state.get("plan_steps", [])
    index = state.get("plan_index", 0)
    current = steps[index] if index < len(steps) else {"route": state.get("route", ""), "task": ""}
    outputs: list[dict[str, Any]] = [
        *state.get("step_outputs", []),
        {
            "route": current.get("route", ""),
            "task": current.get("task", ""),
            "output": state.get("final_text", "")[:STEP_RECORD_CHARS],
            "citations": state.get("citations", []),
            "chart": state.get("chart", {}),
            "sources": state.get("sources", []),
        },
    ]
    next_index = index + 1
    result: AssistantState = {"step_outputs": outputs, "plan_index": next_index}
    # Step-failure handling: the specialists' degradation paths set
    # `step_error` (the only reliable signal — they return prose, never
    # raise). One replan attempt per turn — INCLUDING a final/single-step
    # failure, where one revision ("try a different specialist") can still
    # rescue the answer; after the budget is spent, stop marching a broken
    # plan forward and surface what happened honestly.
    if state.get("step_error", False):
        from app.core.trace import tag

        if state.get("replan_count", 0) == 0:
            log.warning("plan.step_failed_replanning", step=index + 1)
            tag("plan:replanning")
            return {**result, "needs_replan": True, "step_error": False}
        if next_index < len(steps):
            log.warning("plan.step_failed_early_exit", step=index + 1)
            tag("plan:early_exit")
            return {**result, "plan_index": len(steps), "step_error": False}
        result["step_error"] = False  # budget spent on the last step: end normally
    if next_index < len(steps):
        next_step = steps[next_index]
        result["route"] = next_step["route"]
        result["current_task"] = next_step["task"]
        # each step starts CLEAN: without these resets, step 1's citations/
        # chart/sources bleed into step 2's record and compose duplicates them
        result["final_text"] = ""
        result["step_error"] = False  # a stale flag would re-trigger replan next advance
        result["citations"] = []
        result["tool_calls"] = []
        result["sources"] = []
        result["chart"] = {}
        result["data_as_of"] = ""
        result["tool_iterations"] = 0
        result["tool_transcript"] = []
        result["pending_tool_calls"] = []
        result["research_subs"] = []
        result["research_sources"] = []
        result["research_pages"] = []
        log.info("plan.advance", step=next_index + 1, of=len(steps), route=next_step["route"])
        emit_frame(
            {
                "type": "routing",
                "route": next_step["route"],
                "reason": next_step["task"][:160],
                "step": next_index + 1,
                "of": len(steps),
            }
        )
    return result


# Structural conformance check — advance_node must satisfy the Specialist protocol.
_: Specialist = advance_node


def after_step(state: AssistantState) -> str:
    """Conditional: replan, next specialist, compose (multi-step), or persist."""
    if state.get("needs_replan", False):
        return "replan"
    steps = state.get("plan_steps", [])
    index = state.get("plan_index", 0)
    if index < len(steps):
        route = steps[index].get("route", "")
        return route if route else "persist"
    if len(state.get("step_outputs", [])) > 1:
        return "compose"
    return "persist"
