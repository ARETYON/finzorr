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


_MARKER_TOKEN = "[{n}]"  # noqa: S105 — a citation marker template, not a secret


def renumber_steps(
    outputs: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Global citation renumbering across steps.

    Two steps that each cite `[1]` would collide in the composed answer
    (`[1]` meaning two URLs). Remap ONLY the markers present in each step's
    own citations list (never a bare `\\[\\d+\\]` sweep — prose and code
    blocks legitimately contain bracketed numbers), rewriting both the step
    text and the citation `marker` fields to one global sequence.
    """
    texts: list[str] = []
    merged: list[dict[str, Any]] = []
    counter = 0
    for output in outputs:
        text = str(output.get("output", ""))
        remapped: list[tuple[str, str]] = []
        for citation in output.get("citations", []):
            if not isinstance(citation, dict):
                continue
            counter += 1
            old_marker = str(citation.get("marker", ""))
            new_marker = _MARKER_TOKEN.format(n=counter)
            if old_marker and old_marker != new_marker:
                remapped.append((old_marker, new_marker))
            merged.append({**citation, "marker": new_marker})
        # placeholder two-phase swap so [1]->[3] can't collide with a later
        # [3]->[5] rewrite in the same text
        for i, (old_marker, _new) in enumerate(remapped):
            text = text.replace(old_marker, f"\x00{i}\x00")
        for i, (_old, new_marker) in enumerate(remapped):
            text = text.replace(f"\x00{i}\x00", new_marker)
        texts.append(text)
    return texts, merged


async def compose_node(state: AssistantState) -> AssistantState:
    """Stream the combined answer; degrade to concatenated step outputs."""
    outputs = state.get("step_outputs", [])
    renumbered_texts, merged_citations = renumber_steps(outputs)
    steps_text = "\n\n".join(
        f"### Step {i} — {o.get('route', '?')}: {o.get('task', '')}\n"
        f"{text[:_STEP_CHARS]}"
        for i, (o, text) in enumerate(zip(outputs, renumbered_texts, strict=True), start=1)
    )
    # step charts/sources must survive into the final payload — per-step
    # resets would otherwise silently drop step 1's price chart
    merged_sources: list[str] = []
    for output in outputs:
        for source in output.get("sources", []):
            if source not in merged_sources:
                merged_sources.append(source)
    merged_chart: dict[str, Any] = {}
    for output in outputs:
        chart = output.get("chart")
        if isinstance(chart, dict) and chart:
            merged_chart = chart
            break

    from app.core.trace import mark

    mark(
        steps=len(outputs),
        citations=len(merged_citations),
        sources=len(merged_sources),
    )
    plan_len = len(state.get("plan_steps", [])) or len(outputs)
    emit_frame(
        {
            "type": "routing",
            "route": "compose",
            "reason": "combining step results",
            "step": plan_len,
            "of": plan_len,
        }
    )

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
        from app.core.trace import tag as _tag

        _tag("degraded:compose_fallback")
        log.error("node.compose.error", error=str(exc))
        # renumbered texts here too, or the fallback answer has colliding markers
        final = "\n\n---\n\n".join(renumbered_texts)
    # citation validity on the synthesized text against the MERGED/renumbered
    # citation set — a different marker space than any single step's own
    # (observe-only, never mangles the answer)
    from app.core.trace import tag as _tag
    from app.domain.citations import find_invalid_markers
    from app.domain.guard import screen_output

    invalid = find_invalid_markers(final, len(merged_citations))
    if invalid:
        _tag("citation:invalid")
        log.warning("node.compose.invalid_citations", markers=invalid)
    if screen_output(final) == "suspicious":
        _tag("output:suspicious")
    return {
        "final_text": final,
        "citations": merged_citations,
        "sources": merged_sources,
        "chart": merged_chart,
    }
