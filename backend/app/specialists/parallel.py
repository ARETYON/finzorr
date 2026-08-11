"""Parallel plan execution: independent steps fan out via the Send API.

`route_selector` returns one `Send("spec_runner", payload)` per step when
the supervisor marked the plan parallel; each branch runs its specialist
concurrently in its own superstep task and writes ONLY `parallel_outputs`
(an additive channel) — two branches writing `final_text` would raise
InvalidUpdateError. `join` orders the branch records into `step_outputs`
for compose. Only side-effect-free single-node routes are fan-out eligible
(no tools loop → no interrupt inside a branch; no research pipeline).
Branch token streams are suppressed by the specialists themselves (the
stream writer is ambient) so concurrent branches can't garble the UI.
"""

from typing import Any

from app.core.logging import log
from app.core.otel import span
from app.graph.state import AssistantState
from app.specialists.base import Specialist

# Send payloads REPLACE the node input (no merge with graph state), so the
# fan-out overlay must carry full state; these are the routes whose node fns
# are single-shot and side-effect-free.
PARALLEL_ROUTES: frozenset[str] = frozenset({"general_chat", "web_search", "nl2sql", "rag"})


async def spec_runner_node(state: AssistantState) -> AssistantState:
    """Run one branch's specialist; repackage its ENTIRE return into
    parallel_outputs (never let branch dicts race on LastValue channels)."""
    route = state.get("route", "general_chat")
    index = int(state.get("plan_index", 0))
    task = state.get("current_task", "")
    from app.specialists.general_chat import general_chat_node
    from app.specialists.nl2sql import nl2sql_node
    from app.specialists.rag import rag_node
    from app.specialists.web_search import web_search_node

    dispatch = {
        "general_chat": general_chat_node,
        "web_search": web_search_node,
        "nl2sql": nl2sql_node,
        "rag": rag_node,
    }
    node = dispatch.get(route, general_chat_node)
    with span("node", node=f"parallel:{route}"):
        try:
            out = await node(state)
        except Exception as exc:  # noqa: BLE001 — one branch must not kill the fan-out
            log.error("parallel.branch_failed", route=route, error=str(exc))
            out = {"final_text": f"(the {route} step failed)", "step_error": True}
    from app.core.trace import mark, tag

    mark(branch_route=route)
    if out.get("step_error"):
        tag("degraded:branch_failed")
    record: dict[str, Any] = {
        "step_index": index,
        "route": route,
        "task": task,
        "output": str(out.get("final_text", ""))[:2000],
        "citations": out.get("citations", []),
        "chart": out.get("chart", {}),
        "sources": out.get("sources", []),
        "step_error": bool(out.get("step_error", False)),
    }
    return {"parallel_outputs": [record]}


async def join_node(state: AssistantState) -> AssistantState:
    """Barrier after the fan-out: order branch records into step_outputs."""
    records = sorted(
        state.get("parallel_outputs") or [], key=lambda r: int(r.get("step_index", 0))
    )
    log.info("parallel.joined", branches=len(records))
    return {
        "step_outputs": [
            {k: v for k, v in r.items() if k != "step_index"} for r in records
        ],
        "plan_index": len(state.get("plan_steps", [])),
    }


# Structural conformance check — both fan-out nodes must satisfy Specialist.
_spec_runner_conforms: Specialist = spec_runner_node
_join_conforms: Specialist = join_node
