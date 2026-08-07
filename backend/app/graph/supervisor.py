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
        version="3",
        template=(
            "{documents}"
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
            "steps in order (max 3); each step's output is given to the next. "
            "Fully INDEPENDENT steps (none needs another's output) using only "
            "general_chat/web_search/nl2sql/rag may add parallel true to run "
            "concurrently.\n"
            'Reply with ONLY JSON: {{"plan": [{{"route": "<specialist>", '
            '"task": "<what this step must do>"}}, ...], "parallel": false, '
            '"reason": "<short>"}}'
        ),
    )
)

MAX_PLAN_STEPS = 3

# routes safe inside a Send fan-out: single-node, side-effect-free, cannot
# interrupt() — tools (HITL+loop), research (pipeline), memory (mutations)
# are excluded by construction
PARALLELIZABLE: frozenset[str] = frozenset({"general_chat", "web_search", "nl2sql", "rag"})

# Referential markers that reveal a later step depends on an earlier one.
# ANCHORED phrases only: bare "above"/"previous"/"based on" collide with
# screener language ("market cap above 500cr", "previous close") and would
# demote legitimately parallel plans. Lexical, not semantic — the honest
# limit is recorded in §20 (semantic detection would cost an LLM pass per
# turn, deliberately not spent).
_DEPENDENT_TASK = re.compile(
    r"(the above|mentioned above|previous (step|result|output)|that result"
    r"|the result|based on (that|the (result|output|previous))|step \d"
    r"|its output)",
    re.IGNORECASE,
)


def _steps_look_dependent(steps: list[dict[str, str]]) -> bool:
    """True when any step BEYOND the first references earlier work — such a
    plan must run sequentially so feed-forward can supply that reference
    (parallel branches see no step_context)."""
    return any(_DEPENDENT_TASK.search(s.get("task", "")) for s in steps[1:])


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


def _mentions_document(message: str, documents: list[str]) -> bool:
    """True when the message names one of the user's uploads (stem match,
    ≥4 chars to avoid trivial hits) — near-certain rag intent."""
    lowered = message.lower()
    for name in documents:
        stem = name.rsplit(".", 1)[0].lower()
        if len(stem) >= 4 and stem in lowered:
            return True
    return False


def keyword_route(message: str, documents: list[str] | None = None) -> str:
    """Deterministic fallback router (works with the LLM fully down)."""
    if _URL_HINT.search(message):
        return "tools"
    if documents and _mentions_document(message, documents):
        return "rag"
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
    documents = state.get("user_documents", [])
    fallback = keyword_route(user_msg, documents)
    # the planner must KNOW uploads exist — without this line a natural
    # content question ("what was Q3 revenue?") never reaches the user's
    # own report (the design gap found live)
    documents_note = (
        "The user has uploaded these documents: "
        + ", ".join(documents[:20])
        + " — questions answerable from them should route to rag.\n\n"
        if documents
        else ""
    )
    decision: dict[str, Any] = {}
    try:
        raw = await complete(
            [
                SystemMessage(
                    content=render_agent_prompt(
                        "supervisor_planner", documents=documents_note
                    )
                ),
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
    parallel = (
        bool(decision.get("parallel", False))
        and len(steps) > 1
        and all(s["route"] in PARALLELIZABLE for s in steps)
        # route class alone can't see dependence — a mislabeled parallel plan
        # silently loses feed-forward, so referential tasks force sequential
        and not _steps_look_dependent(steps)
    )
    return {
        "route": steps[0]["route"],
        "plan_steps": steps,
        "plan_index": 0,
        "current_task": steps[0]["task"],
        "plan_parallel": parallel,
        "route_reason": reason,
    }


def route_selector(state: AssistantState) -> Any:
    """Conditional-edge selector; a parallel plan fans out via Send.

    Send objects bypass the path_map (verified in the langgraph 1.2 source),
    so mixing them with the BRANCHES dict is supported; each Send carries
    FULL state merged with its branch overlay because Send payloads replace,
    not merge.
    """
    if state.get("plan_parallel", False):
        from langgraph.types import Send

        return [
            Send(
                "spec_runner",
                {
                    **state,
                    "route": step["route"],
                    "current_task": step["task"],
                    "plan_index": i,
                    "parallel_branch": True,
                },
            )
            for i, step in enumerate(state.get("plan_steps", []))
        ]
    route = state.get("route", "")
    return route if route in ROUTES else "general_chat"
