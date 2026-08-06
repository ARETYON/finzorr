"""Typed state flowing through the assistant graph.

`messages` is the only accumulating channel (checkpointed across turns);
everything else is per-turn and reset by the turn runner on every invocation.
"""

import operator
from typing import Annotated, Any, TypedDict


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

    # accumulating conversation channel (checkpointer-managed)
    messages: Annotated[list[dict[str, Any]], operator.add]
