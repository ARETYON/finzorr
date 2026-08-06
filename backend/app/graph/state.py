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
    correlation_id: str

    # planner output
    route: str
    plan: list[str]
    route_reason: str

    # tool-loop working state (per-turn; checkpointed per superstep)
    tool_transcript: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_iterations: int

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
