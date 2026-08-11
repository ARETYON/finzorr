"""The Specialist protocol: the structural contract every graph node satisfies.

Every specialist entry-point LangGraph calls (rag_node, general_chat_node,
tools_plan_node, advance_node, compose_node, ...) already has the shape
`async def fn(state: AssistantState) -> AssistantState`. This Protocol
describes exactly that reality — it is deliberately NOT an ABC: Protocol
gives structural typing, meaning every specialist that already matches the
shape satisfies it WITHOUT needing to change their internals or add
inheritance. This makes conformance mypy-checkable without touching
specialist internals.
"""

from typing import Protocol

from app.graph.state import AssistantState


class Specialist(Protocol):
    """Structural contract every specialist satisfies — no inheritance
    required, this makes conformance mypy-checkable without touching
    specialist internals."""

    async def __call__(self, state: AssistantState) -> AssistantState: ...
