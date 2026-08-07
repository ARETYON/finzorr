"""Typed state flowing through the assistant graph.

`messages` is the only cross-turn accumulating channel. Its reducer caps the
list — an unbounded `operator.add` made every checkpoint serialize the entire
conversation history (O(n²) storage per thread over its lifetime). The prompt
window is narrower still (build_history); the cap only bounds storage.

The tool loop's working state (`tool_transcript`, `pending_tool_calls`,
`tool_iterations`) lives in graph state so every LLM round-trip and tool
result is checkpointed as its own superstep — a crash or cancel mid-loop
preserves completed steps instead of losing them all.
"""

from typing import Annotated, Any, TypedDict

MESSAGES_CAP = 60  # newest N chat messages kept in the checkpoint


def merge_parallel(
    existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Additive reducer for fan-out branch records. None-tolerant on BOTH
    sides: langgraph assigns the very first write raw when the channel starts
    MISSING, so a None must still normalize. turn.py resets via
    langgraph.types.Overwrite([])."""
    return [*(existing or []), *(new or [])]


def capped_messages(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append-and-trim reducer for the conversation channel."""
    return (list(existing) + list(new))[-MESSAGES_CAP:]


class AssistantState(TypedDict, total=False):
    # per-turn input
    session_id: str
    user_id: str
    user_name: str
    user_msg: str
    user_documents: list[str]  # ready upload filenames — routing awareness, per-turn
    correlation_id: str
    turn_id: str  # idempotency key for persist (normal + out-of-band paths)
    hitl_enabled: bool  # checkpointer present -> sensitive tools may interrupt

    # planner output — plan_steps is EXECUTABLE: [{route, task}], walked by
    # the advance node with each step's output fed into the next
    route: str
    plan_steps: list[dict[str, str]]
    plan_index: int
    step_outputs: list[dict[str, Any]]
    current_task: str
    route_reason: str
    step_error: bool  # set by a specialist's degradation path (the only failure signal)
    needs_replan: bool
    replan_count: int
    plan_parallel: bool
    parallel_branch: bool  # set inside a Send fan-out branch (suppresses token streams)
    parallel_outputs: Annotated[list[dict[str, Any]], merge_parallel]

    # tool-loop working state (per-turn; checkpointed per superstep)
    tool_transcript: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_iterations: int

    # research-stage working state (per-turn; checkpointed per stage)
    research_subs: list[str]
    research_sources: list[dict[str, str]]
    research_pages: list[str]

    # node output
    final_text: str
    citations: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    data_as_of: str
    sources: list[str]
    chart: dict[str, Any]  # {symbol, period, points[]} when a price chart applies

    # per-user preferences loaded at turn start
    user_instructions: str

    # persist output
    message_id: str

    # accumulating conversation channel (checkpointer-managed, capped)
    messages: Annotated[list[dict[str, Any]], capped_messages]
