"""Compose node: one streamed synthesis when a multi-step plan finishes.

Single-step turns never reach this node (zero added cost/latency for the
common case). Citations from every step are merged so intermediate steps'
sources aren't silently dropped by last-write-wins state keys.
"""

from typing import Any

from app.ai.base import SystemMessage, UserMessage
from app.ai.completion import stream
from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.graph.nodes.common import with_instructions
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame

register(
    AgentPrompt(
        name="compose_synthesis",
        version="1",
        template=(
            "You are finzorr. Several specialist steps just ran to answer the "
            "user's request; their results are below. Write ONE coherent final "
            "answer that uses them all, in the order that reads naturally.\n"
            "- Ground every claim in the step results — never invent numbers.\n"
            "- Keep any [n] citation markers exactly as they appear.\n"
            "- If a step failed or returned nothing useful, say so briefly.\n"
            "- If any result includes finance/investment information, end with: "
            '"This is general information, not investment advice. Market data '
            'may be delayed."\n\n'
            "User request: {user_msg}\n\nSTEP RESULTS:\n{steps}"
        ),
    )
)

_STEP_CHARS = 2000


async def compose_node(state: AssistantState) -> AssistantState:
    """Stream the combined answer; degrade to concatenated step outputs."""
    outputs = state.get("step_outputs", [])
    steps_text = "\n\n".join(
        f"### Step {i} — {o.get('route', '?')}: {o.get('task', '')}\n"
        f"{str(o.get('output', ''))[:_STEP_CHARS]}"
        for i, o in enumerate(outputs, start=1)
    )
    merged_citations: list[dict[str, Any]] = [
        c for o in outputs for c in o.get("citations", []) if isinstance(c, dict)
    ]

    async def on_token(t: str) -> None:
        emit_frame({"type": "token", "delta": t})

    try:
        done = await stream(
            [
                SystemMessage(
                    content=with_instructions(
                        render_agent_prompt(
                            "compose_synthesis",
                            user_msg=state["user_msg"],
                            steps=steps_text,
                        ),
                        state,
                    )
                ),
                UserMessage(content="Write the final combined answer now."),
            ],
            on_token=on_token,
            temperature=0.3,
            max_tokens=1536,
        )
        final = done.text
    except Exception as exc:  # noqa: BLE001 — degrade to raw step outputs
        log.error("node.compose.error", error=str(exc))
        final = "\n\n---\n\n".join(str(o.get("output", "")) for o in outputs)
    return {"final_text": final, "citations": merged_citations}
