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

ROUTES: frozenset[str] = frozenset(
    {"general_chat", "memory", "rag", "web_search", "nl2sql", "tools"}
)

_WEB_HINTS = re.compile(
    r"\b(news|latest|today|yesterday|this week|happened|announce|why did .{0,40}"
    r"(fall|rise|drop|surge|crash|jump))\b",
    re.IGNORECASE,
)
_SQL_HINTS = re.compile(
    r"\b(stocks? (with|having|under|above)|screen(er)?|top \d+|list (all )?stocks?|"
    r"p/?e (under|below|above|less|greater)|dividend yield (above|over|under)|"
    r"market cap (above|over|under|between))\b",
    re.IGNORECASE,
)
_TOOL_HINTS = re.compile(
    r"\b(price|quote|trading at|share price|52.week|overview of|fundamentals of|"
    r"history|historical|performance of|volume|my (portfolio|holdings)|"
    r"deep research|research report)\b",
    re.IGNORECASE,
)
_RAG_HINTS = re.compile(
    r"\b(what is|what does|what are|explain|define|meaning of|my (document|pdf|file|"
    r"contract|report)|uploaded)\b",
    re.IGNORECASE,
)
_MEMORY_HINTS = re.compile(
    r"\b(watch\s?list|track|untrack|my list|alert me|set (an? )?alert|notify me|"
    r"every (day|week|morning|evening)|daily at|remind me)\b",
    re.IGNORECASE,
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

register(
    AgentPrompt(
        name="supervisor_planner",
        version="1",
        template=(
            "You route user messages for an assistant with these specialists:\n"
            "- general_chat: anything conversational, coding, writing, general knowledge\n"
            "- memory: the user's watchlist, PRICE ALERTS ('alert me when...'), and "
            "scheduled/recurring tasks ('every day...')\n"
            "- rag: definitions/concepts (finance glossary) or questions about the "
            "user's uploaded documents\n"
            "- web_search: fresh news / current events / anything needing today's info\n"
            "- nl2sql: screening/filtering MANY stocks by metrics (P/E, yield, "
            "market cap...)\n"
            "- tools: live price/quote/fundamentals/history of SPECIFIC stocks, the "
            "user's PORTFOLIO/holdings performance, deep research reports, or "
            "reading/summarizing a URL the user pasted\n\n"
            'Reply with ONLY JSON: {{"route": "<one of the six>", '
            '"plan": ["<step>", ...], "reason": "<short>"}}'
        ),
    )
)


_URL_HINT = re.compile(r"https?://\S+", re.IGNORECASE)


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


async def plan_and_route(state: AssistantState) -> AssistantState:
    """One routing LLM call with the keyword router as safety net."""
    user_msg = state["user_msg"]
    fallback = keyword_route(user_msg)
    decision: dict[str, Any] = {}
    try:
        raw = await complete(
            [
                SystemMessage(content=render_agent_prompt("supervisor_planner")),
                UserMessage(content=user_msg),
            ],
            model=settings.SUPERVISOR_MODEL or None,
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        decision = _parse_decision(raw)
    except Exception as exc:  # noqa: BLE001 — fallback carries the turn
        log.warning("supervisor.llm_failed", error=str(exc))
    route = str(decision.get("route", ""))
    if route not in ROUTES:
        route = fallback
    plan = decision.get("plan") or [user_msg]
    reason = str(decision.get("reason", "keyword fallback"))
    log.info("supervisor.route", route=route, reason=reason[:100])
    return {"route": route, "plan": [str(p) for p in plan], "route_reason": reason}


def route_selector(state: AssistantState) -> str:
    """Conditional-edge selector; defaults to general_chat."""
    route = state.get("route", "")
    return route if route in ROUTES else "general_chat"
