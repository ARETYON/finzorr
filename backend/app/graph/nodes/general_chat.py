"""Default route: direct LLM conversation, streamed token-by-token."""

from typing import Any

from app.ai.base import AssistantMessage, ChatMessage, SystemMessage, UserMessage
from app.ai.completion import stream
from app.core.logging import log
from app.core.prompt_registry import render_agent_prompt
from app.graph.callbacks import emit
from app.graph.state import AssistantState

HISTORY_WINDOW = 12  # last N turns carried into the prompt


def build_history(messages: list[dict[str, Any]]) -> list[ChatMessage]:
    """Convert persisted message dicts into provider messages (sliding window)."""
    history: list[ChatMessage] = []
    for m in messages[-HISTORY_WINDOW:]:
        role, content = m.get("role"), str(m.get("content", ""))
        if role == "user":
            history.append(UserMessage(content=content))
        elif role == "assistant":
            history.append(AssistantMessage(content=content))
    return history


async def general_chat_node(state: AssistantState) -> AssistantState:
    """Stream a direct answer; degrade to a friendly error on any failure."""
    session_id = state["session_id"]

    async def on_token(t: str) -> None:
        await emit(session_id, {"type": "token", "delta": t})

    system = SystemMessage(
        content=render_agent_prompt("general_chat_system", user_name=state.get("user_name", "there"))
    )
    msgs: list[ChatMessage] = [system, *build_history(state.get("messages", []))]
    msgs.append(UserMessage(content=state["user_msg"]))
    try:
        done = await stream(msgs, on_token=on_token, temperature=0.7, max_tokens=2048)
        return {"final_text": done.text, "route": "general_chat"}
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the turn
        log.error("node.general_chat.error", error=str(exc))
        return {
            "final_text": (
                "I couldn't reach the language model just now. Please try again in a moment."
            ),
            "route": "general_chat",
        }
