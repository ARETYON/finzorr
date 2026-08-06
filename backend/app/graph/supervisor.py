"""Supervisor: one LLM call -> {route, plan, reason}, keyword fallback beneath.

The deterministic keyword router keeps routing alive with the LLM down and is
unit-tested without any live dependency. Check order matters: web (news) ->
nl2sql (screening) -> tools (live quotes) -> rag (concepts/documents) ->
memory (watchlist) -> general_chat (default).
"""

import json
import re
from typing import Any

from app.ai.base import SystemMessage, UserMessage
from app.ai.completion import complete
from app.core.config import settings
from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame

ROUTES: frozenset[str] = frozenset(
    {"general_chat", "memory", "rag", "web_search", "nl2sql", "tools", "research"}
)

_WEB_HINTS = re.compile(
    r"\b(news|latest|today|yesterday|this week|happened|announce|why did .{0,40}"
    r"(fall|rise|drop|surge|crash|jump))\b",
    re.IGNORECASE,
)
_SQL_HINTS = re.compile(
    r"\b(stocks? (with|having|under|above)|which stocks|screen(er)?|top \d+|"
    r"list (all )?(stocks?|banks?|companies)|sorted by|"
    r"p/?e (under|below|above|less|greater)|dividend yield (above|over|under)|"
    r"(roe|eps|pb ratio) (above|over|under|below|greater|less)|"
    r"market cap (above|over|under|between))\b",
    re.IGNORECASE,
)
_TOOL_HINTS = re.compile(
    r"\b(price|quote|chart|trading at|share price|52.week|overview of|fundamentals of|"
    r"history|historical|performance of|volume|my (portfolio|holdings)|"
    r"run (python|code)|execute (python|code)|"
    r"compute|calculate)\b",
    re.IGNORECASE,
)
_RAG_HINTS = re.compile(
    r"\b(what is|what does|what are|explain|define|meaning of|(search )?my "
    r"(documents?|pdf|files?|contract|reports?)|uploaded)\b",
    re.IGNORECASE,
)
_MEMORY_HINTS = re.compile(
    r"\b(watch\s?list|track|untrack|my list|alert me|set (an? )?(price )?alert|"
    r"price alert|notify me|every (day|week|morning|evening)|daily at|remind me|"
    r"remember (that|my|i))\b",
    re.IGNORECASE,
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

register(
    AgentPrompt(
        name="supervisor_planner",
        version="2",
        template=(
            "You plan how an assistant answers, using these specialists:\n"
            "- general_chat: anything conversational, coding, writing, general knowledge\n"
            "- memory: the user's watchlist, PRICE ALERTS ('alert me when...'), and "
            "scheduled/recurring tasks ('every day...')\n"
            "- rag: definitions/concepts (finance glossary) or questions about the "
            "user's uploaded documents\n"
            "- web_search: fresh news / current events / anything needing today's info\n"
            "- nl2sql: screening/filtering MANY stocks by metrics (P/E, yield, "
            "market cap...)\n"
            "- tools: live price/quote/fundamentals/history of SPECIFIC stocks, the "
            "user's PORTFOLIO/holdings performance, running/executing Python code "
            "or calculations, or reading/summarizing a URL the user pasted\n"
            "- research: DEEP research — multi-source reports, thorough "
            "comparisons, anything needing several searches and page reads\n\n"
            "Most messages need ONE specialist — return a single step. When the "
            "request genuinely chains two or three (e.g. 'find the latest news on X "
            "and then show its current price' -> web_search then tools), return the "
            "steps in order (max 3); each step's output is given to the next.\n"
            'Reply with ONLY JSON: {{"plan": [{{"route": "<specialist>", '
            '"task": "<what this step must do>"}}, ...], "reason": "<short>"}}'
        ),
    )
)

MAX_PLAN_STEPS = 3


def validate_plan(raw_plan: Any, user_msg: str) -> list[dict[str, str]]:
    """Coerce the LLM's plan into executable [{route, task}] steps.

    Tolerates the v1 shape (list of strings = single step) and any garbage
    (-> empty, caller falls back to keyword routing). Never raises.
    """
    if not isinstance(raw_plan, list):
        return []
    steps: list[dict[str, str]] = []
    for entry in raw_plan[:MAX_PLAN_STEPS]:
        if isinstance(entry, dict):
            route = str(entry.get("route", ""))
            task = str(entry.get("task", "")).strip() or user_msg
            if route in ROUTES:
                steps.append({"route": route, "task": task})
        # v1 emitted plain strings — informational only, not a routed step
    return steps


_URL_HINT = re.compile(r"https?://\S+", re.IGNORECASE)
_RESEARCH_HINTS = re.compile(
    r"\b(deep research|research report|do (deep )?research|thorough (comparison|analysis)|"
    r"research (on|about|into))\b",
    re.IGNORECASE,
)


def keyword_route(message: str) -> str:
    """Deterministic fallback router (works with the LLM fully down)."""
    if _URL_HINT.search(message):
        return "tools"
    if _MEMORY_HINTS.search(message):
        return "memory"
    if _SQL_HINTS.search(message):
        return "nl2sql"
    if _WEB_HINTS.search(message):
        return "web_search"
    if _RESEARCH_HINTS.search(message):
        return "research"
    if _TOOL_HINTS.search(message):
        return "tools"
    if _RAG_HINTS.search(message):
        return "rag"
    return "general_chat"


def _parse_decision(raw: str) -> dict[str, Any]:
    """json.loads -> regex extraction -> empty dict (never raises)."""
    for candidate in (raw, *([m.group(0)] if (m := _JSON_BLOCK.search(raw)) else [])):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return {}


_CONTEXT_CHARS = 200  # per prior message, in the routing prompt


def _routing_context(state: AssistantState) -> str:
    """The previous exchange, truncated — follow-ups like "and its P/E?"
    misroute deterministically when the router sees only the new message."""
    history = state.get("messages", [])
    prior = [m for m in history if m.get("role") in ("user", "assistant")][-2:]
    if not prior:
        return ""
    lines = [f"{m['role']}: {str(m.get('content', ''))[:_CONTEXT_CHARS]}" for m in prior]
    return "Previous exchange (context only):\n" + "\n".join(lines) + "\n\n"


async def plan_and_route(state: AssistantState) -> AssistantState:
    """One planning LLM call with the keyword router as safety net.

    Output is an EXECUTABLE plan: 1-3 [{route, task}] steps that the advance
    node walks, feeding each step's output into the next. The deterministic
    keyword fallback always yields a single step, so a total LLM outage
    degrades to classifier behavior, never to a dead turn.
    """
    user_msg = state["user_msg"]
    fallback = keyword_route(user_msg)
    decision: dict[str, Any] = {}
    try:
        raw = await complete(
            [
                SystemMessage(content=render_agent_prompt("supervisor_planner")),
                UserMessage(content=f"{_routing_context(state)}Message to route: {user_msg}"),
            ],
            model=settings.SUPERVISOR_MODEL or None,
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        decision = _parse_decision(raw)
    except Exception as exc:  # noqa: BLE001 — fallback carries the turn
        log.warning("supervisor.llm_failed", error=str(exc))
    steps = validate_plan(decision.get("plan"), user_msg)
    # v1-era shape ({"route": ...} with no step objects) still routes
    if not steps:
        route = str(decision.get("route", ""))
        if route not in ROUTES:
            route = fallback
        steps = [{"route": route, "task": user_msg}]
    reason = str(decision.get("reason", "keyword fallback"))
    log.info(
        "supervisor.route",
        route=steps[0]["route"],
        steps=len(steps),
        reason=reason[:100],
    )
    emit_frame(
        {
            "type": "routing",
            "route": steps[0]["route"],
            "reason": reason[:160],
            "step": 1,
            "of": len(steps),
        }
    )
    return {
        "route": steps[0]["route"],
        "plan_steps": steps,
        "plan_index": 0,
        "current_task": steps[0]["task"],
        "route_reason": reason,
    }


def route_selector(state: AssistantState) -> str:
    """Conditional-edge selector; defaults to general_chat."""
    route = state.get("route", "")
    return route if route in ROUTES else "general_chat"
