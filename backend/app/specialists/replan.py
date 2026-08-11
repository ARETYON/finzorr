"""Replan node: one revision attempt when a plan step fails mid-flight.

Fixed plans marched blindly past failed steps ("fire-and-forget"). When a
specialist's degradation path sets `step_error`, `advance` routes here ONCE
per turn: a single LLM call sees the original request, the plan so far
(including the failure), and proposes revised REMAINING steps. No revision →
early exit, with the failure surfaced honestly by compose/persist.
Sequential plans only — a parallel fan-out is never replanned.
"""

import json
from typing import Any

from app.ai.base import SystemMessage, UserMessage
from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame
from app.graph.supervisor import validate_plan
from app.specialists.base import Specialist

register(
    AgentPrompt(
        name="replan",
        version="1",
        template=(
            "A multi-step plan hit a failed step. Propose revised REMAINING "
            "steps (max {budget}) that still answer the user, working around "
            "the failure — e.g. a different specialist, or a simpler ask. If "
            "nothing sensible remains, reply with an empty plan.\n"
            "Specialists: general_chat, memory, rag, web_search, nl2sql, "
            "tools, research.\n"
            'Reply ONLY JSON: {{"plan": [{{"route": "<specialist>", '
            '"task": "<step task>"}}, ...], "reason": "<short>"}}\n\n'
            "User request: {user_msg}\n\nPlan so far (last step FAILED):\n{history}"
        ),
    )
)

_REPLAN_TIMEOUT_S = 30.0


async def replan_node(state: AssistantState) -> AssistantState:
    """Revise the remaining steps once; empty revision = honest early exit."""
    from app.ai.completion import complete

    steps = state.get("plan_steps", [])
    index = state.get("plan_index", 0)  # advance already moved past the failed step
    history = "\n".join(
        f"step {i} ({o.get('route')}): {o.get('task')} -> "
        f"{str(o.get('output', ''))[:200]}"
        for i, o in enumerate(state.get("step_outputs", []), start=1)
    )
    budget = max(len(steps) - index, 1)
    revised: list[dict[str, str]] = []
    revision_reason = ""
    try:
        raw = await complete(
            [
                SystemMessage(
                    content=render_agent_prompt(
                        "replan",
                        budget=str(budget),
                        user_msg=state["user_msg"],
                        history=history,
                    )
                ),
                UserMessage(content="Revise the remaining steps now."),
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
            overall_timeout_s=_REPLAN_TIMEOUT_S,
        )
        decision: dict[str, Any] = json.loads(raw) if raw.strip().startswith("{") else {}
        revision_reason = str(decision.get("reason", ""))
        revised = validate_plan(decision.get("plan"), state["user_msg"])[:budget]
    except Exception as exc:  # noqa: BLE001 — no revision = early exit
        log.warning("replan.failed", error=str(exc))

    from app.core.trace import mark, tag

    mark(replan_reason=revision_reason[:200], revised=len(revised))
    result: AssistantState = {"needs_replan": False, "replan_count": 1, "step_error": False}
    if not revised:
        tag("replan:empty")
        log.info("replan.empty_early_exit")
        result["plan_index"] = len(steps)
        return result
    new_steps = [*steps[:index], *revised]
    result["plan_steps"] = new_steps
    result["plan_index"] = index
    result["route"] = revised[0]["route"]
    result["current_task"] = revised[0]["task"]
    # same clean-slate discipline as advance when arming a step
    result["final_text"] = ""
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
    log.info("replan.revised", remaining=len(revised), route=revised[0]["route"])
    emit_frame(
        {
            "type": "routing",
            "route": revised[0]["route"],
            "reason": f"replanned: {revised[0]['task'][:120]}",
            "step": index + 1,
            "of": len(new_steps),
        }
    )
    return result


# Structural conformance check — replan_node must satisfy the Specialist protocol.
_: Specialist = replan_node
