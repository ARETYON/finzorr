"""Tools route: the agent loop as CHECKPOINTED graph nodes.

`tools_plan` (one LLM call) and `tools_exec` (concurrent tool dispatch)
alternate via conditional edges; the loop's transcript and pending calls live
in graph state, so every round-trip is its own checkpointed superstep — a
crash or cancel mid-loop keeps the completed steps, `aget_state` can inspect
a live trajectory, and resume re-enters exactly where it stopped. Capped at
6 iterations (a finance turn resolves in 1-3 round-trips).
"""

from datetime import UTC, datetime
from functools import partial
from typing import Any

from app.ai.base import (
    AssistantMessage,
    ChatMessage,
    SystemMessage,
    ToolCallRequest,
    ToolResultMessage,
    UserMessage,
)
from app.ai.completion import stream
from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.core.request_context import user_context
from app.graph.nodes.common import with_instructions
from app.graph.nodes.general_chat import build_history
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame
from app.tools_registry import (
    market_tools,  # noqa: F401 — registers the family
    portfolio_tools,  # noqa: F401 — registers analyze_portfolio
    research_tools,  # noqa: F401 — registers deep_research
    web_tools,  # noqa: F401 — registers read_url
)
from app.tools_registry.dispatcher import all_tools, dispatch_all

MAX_ITERATIONS = 6
RESULT_PREVIEW_CHARS = 300

register(
    AgentPrompt(
        name="tools_system",
        version="1",
        template=(
            "You are finzorr's market-data agent for Indian stocks (NSE/BSE).\n"
            "- ALWAYS call tools for prices, fundamentals, or history — never answer "
            "from memory for live market data.\n"
            "- If a company name is ambiguous, call search_symbol first.\n"
            "- Portfolio/holdings questions -> call analyze_portfolio.\n"
            "- Requests for research/reports/comparisons -> call deep_research.\n"
            "- A pasted URL -> call read_url.\n"
            "- Report numbers exactly as returned; mention the data may be delayed.\n"
            "- End finance answers with: \"This is general information, not investment "
            "advice. Market data may be delayed.\"\n"
            "- User's name: {user_name}."
        ),
    )
)


# --- transcript (de)serialization — graph state must be JSON-serializable ---

def _serialize_assistant(text: str, tool_calls: list[ToolCallRequest]) -> dict[str, Any]:
    return {
        "kind": "assistant",
        "content": text,
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments_json": tc.arguments_json}
            for tc in tool_calls
        ],
    }


def _serialize_tool_result(tool_call_id: str, content: str) -> dict[str, Any]:
    return {"kind": "tool", "tool_call_id": tool_call_id, "content": content}


def _deserialize(entry: dict[str, Any]) -> ChatMessage:
    if entry.get("kind") == "assistant":
        return AssistantMessage(
            content=str(entry.get("content", "")),
            tool_calls=[
                ToolCallRequest(
                    id=str(tc.get("id", "")),
                    name=str(tc.get("name", "")),
                    arguments_json=str(tc.get("arguments_json", "")),
                )
                for tc in entry.get("tool_calls", [])
            ],
        )
    return ToolResultMessage(
        tool_call_id=str(entry.get("tool_call_id", "")),
        content=str(entry.get("content", "")),
    )


def _tool_call_log(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair each assistant tool call with its result for the WS payload."""
    results = {
        e["tool_call_id"]: str(e.get("content", ""))
        for e in transcript
        if e.get("kind") == "tool"
    }
    entries: list[dict[str, Any]] = []
    for e in transcript:
        if e.get("kind") != "assistant":
            continue
        for tc in e.get("tool_calls", []):
            request = ToolCallRequest(
                id=str(tc.get("id", "")),
                name=str(tc.get("name", "")),
                arguments_json=str(tc.get("arguments_json", "")),
            )
            entries.append(
                {
                    "name": request.name,
                    "arguments": request.arguments,
                    "result": results.get(request.id, "")[:RESULT_PREVIEW_CHARS],
                }
            )
    return entries


async def _chart_for(tool_call_log: list[dict[str, Any]]) -> dict[str, Any]:
    """If history was fetched this turn, attach the full series (cache hit) so
    the frontend can render an inline price chart."""
    for tc in tool_call_log:
        if tc.get("name") == "get_historical_prices":
            symbol = str(tc.get("arguments", {}).get("symbol", "")).upper()
            period = str(tc.get("arguments", {}).get("period", "6mo"))
            if not symbol:
                continue
            try:
                from app.market_data.cache import HISTORY_TTL_S, cached_json

                points = await cached_json(
                    f"hist:{symbol}:{period}",
                    HISTORY_TTL_S,
                    partial(_history_series, symbol, period),
                )
                return {"symbol": symbol, "period": period, "points": points}
            except Exception:  # noqa: BLE001 — chart is decorative, never fatal
                return {}
    return {}


async def _history_series(symbol: str, period: str) -> list[dict[str, Any]]:
    from app.tools_registry.market_tools import _history_list

    return await _history_list(symbol, period)


def _finalize(state: AssistantState, final_text: str) -> AssistantState:
    tool_call_log = _tool_call_log(state.get("tool_transcript", []))
    return {
        "final_text": final_text,
        "route": "tools",
        "tool_calls": tool_call_log,
        "data_as_of": datetime.now(UTC).isoformat(),
        "sources": ["Yahoo Finance"] if tool_call_log else [],
        "pending_tool_calls": [],
    }


async def tools_plan_node(state: AssistantState) -> AssistantState:
    """One LLM round-trip: either request tools or produce the final answer."""
    iterations = state.get("tool_iterations", 0)
    if iterations >= MAX_ITERATIONS:
        log.warning("node.tools.iteration_cap", session_id=state["session_id"])
        return _finalize(
            state,
            "I gathered partial data but hit my tool-use limit for one turn — "
            "please ask a more specific question.",
        )

    system_content = render_agent_prompt("tools_system", user_name=state.get("user_name", "there"))
    system_content = with_instructions(system_content, state)
    msgs: list[ChatMessage] = [
        SystemMessage(content=system_content),
        *build_history(state.get("messages", [])),
        UserMessage(content=state["user_msg"]),
        *[_deserialize(e) for e in state.get("tool_transcript", [])],
    ]

    async def on_token(t: str) -> None:
        emit_frame({"type": "token", "delta": t})

    try:
        done = await stream(
            msgs, on_token=on_token, tools=all_tools(), temperature=0.2, max_tokens=1536
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the turn
        log.error("node.tools.error", error=str(exc))
        return _finalize(
            state, "I couldn't fetch market data right now. Please try again shortly."
        )

    if not done.tool_calls:
        result = _finalize(state, done.text)
        result["chart"] = await _chart_for(result.get("tool_calls", []))
        return result

    for tc in done.tool_calls:
        emit_frame({"type": "tool_call", "name": tc.name, "arguments": tc.arguments})
    return {
        "tool_transcript": [
            *state.get("tool_transcript", []),
            _serialize_assistant(done.text, done.tool_calls),
        ],
        "pending_tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in done.tool_calls
        ],
        "tool_iterations": iterations + 1,
    }


async def tools_exec_node(state: AssistantState) -> AssistantState:
    """Dispatch every pending tool call concurrently; results never raise."""
    pending = state.get("pending_tool_calls", [])
    # Identity bound only while tools execute — set/reset as a pair so it can
    # never leak across users in a shared task (scheduler).
    with user_context(state.get("user_id", "")):
        results = await dispatch_all(
            [(str(c["name"]), dict(c.get("arguments", {}))) for c in pending]
        )
    return {
        "tool_transcript": [
            *state.get("tool_transcript", []),
            *[
                _serialize_tool_result(str(call["id"]), result)
                for call, result in zip(pending, results, strict=True)
            ],
        ],
        "pending_tool_calls": [],
    }


def tools_next(state: AssistantState) -> str:
    """Conditional edge: execute pending calls, or the answer is ready."""
    return "tools_exec" if state.get("pending_tool_calls") else "persist"
