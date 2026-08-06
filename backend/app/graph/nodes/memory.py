"""Memory route: the watchlist secretary.

Gathers watchlist + recent history in parallel, asks the LLM for a JSON
contract {message, actions[]}, applies watchlist actions idempotently, and
streams only the human-facing message (the reply is JSON, so no token
streaming — the parsed message is emitted once).
"""

import json
import re
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.ai.base import SystemMessage, UserMessage
from app.ai.completion import stream
from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.db.session import SessionLocal
from app.graph.state import AssistantState
from app.graph.streaming import emit_frame
from app.models.price_alert import PriceAlert
from app.models.scheduled_task import ScheduledTask
from app.models.watchlist_item import WatchlistItem

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

register(
    AgentPrompt(
        name="memory_system",
        version="1",
        template=(
            "You are finzorr's secretary for {user_name}.\n"
            "Current watchlist: {watchlist}\n"
            "Active price alerts: {alerts}\n\n"
            "Reply with ONLY a JSON object:\n"
            '{{"message": "<friendly reply>", "actions": [<zero or more>]}}\n'
            "Action shapes:\n"
            '- {{"type": "watchlist_add"|"watchlist_remove", "symbol": "<TICKER>"}}\n'
            '- {{"type": "alert_create", "symbol": "<TICKER>", '
            '"direction": "above"|"below", "target": <number>}}\n'
            '- {{"type": "task_create", "spec": "daily@HH:MM"|"weekly@D@HH:MM", '
            '"prompt": "<what to do>"}}  (D: 0=Mon..6=Sun, IST times)\n'
            "Rules:\n"
            "- Mutations -> emit the action AND confirm in message.\n"
            "- Questions -> answer from the context above.\n"
            "- Resolve company names to tickers (e.g. Infosys -> INFY).\n"
            "- No investment recommendations; if asked, decline politely."
        ),
    )
)


def parse_reply(raw: str) -> dict[str, Any]:
    """json.loads -> regex block -> plain-text degrade (never raises)."""
    for candidate in (raw, *( [m.group(0)] if (m := _JSON_BLOCK.search(raw)) else [] )):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "message" in parsed:
                return parsed
        except json.JSONDecodeError:
            continue
    return {"message": raw.strip() or "Done.", "actions": []}


async def _get_watchlist(user_id: uuid.UUID) -> list[str]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(WatchlistItem.symbol).where(WatchlistItem.user_id == user_id)
        )
        return [row[0] for row in result]


async def _get_alerts(user_id: uuid.UUID) -> list[str]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(PriceAlert).where(
                PriceAlert.user_id == user_id, PriceAlert.active.is_(True)
            )
        )
        return [f"{a.symbol} {a.direction} {a.target}" for a in result.scalars()]


async def _apply_actions(user_id: uuid.UUID, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Idempotent watchlist mutations; per-action failures are isolated."""
    applied: list[dict[str, Any]] = []
    async with SessionLocal() as db:
        for action in actions:
            try:
                symbol = str(action.get("symbol", "")).upper().strip()
                if action.get("type") != "task_create" and (not symbol or len(symbol) > 32):
                    continue
                if action.get("type") == "watchlist_add":
                    await db.execute(
                        pg_insert(WatchlistItem)
                        .values(user_id=user_id, symbol=symbol, exchange="NSE")
                        .on_conflict_do_nothing(index_elements=["user_id", "symbol"])
                    )
                    applied.append({"type": "watchlist_add", "symbol": symbol})
                elif action.get("type") == "watchlist_remove":
                    await db.execute(
                        delete(WatchlistItem).where(
                            WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol
                        )
                    )
                    applied.append({"type": "watchlist_remove", "symbol": symbol})
                elif action.get("type") == "alert_create":
                    direction = str(action.get("direction", "above"))
                    target = float(action.get("target", 0))
                    if direction in ("above", "below") and target > 0:
                        db.add(
                            PriceAlert(
                                user_id=user_id,
                                symbol=symbol,
                                direction=direction,
                                target=target,
                            )
                        )
                        applied.append(
                            {"type": "alert_create", "symbol": symbol, "target": target}
                        )
                elif action.get("type") == "task_create":
                    spec = str(action.get("spec", "")).strip()
                    prompt = str(action.get("prompt", "")).strip()
                    if spec.split("@")[0] in ("daily", "weekly") and prompt:
                        db.add(ScheduledTask(user_id=user_id, spec=spec, prompt=prompt))
                        applied.append({"type": "task_create", "spec": spec})
            except Exception as exc:  # noqa: BLE001 — one bad action != dead turn
                log.warning("node.memory.action_failed", error=str(exc))
        await db.commit()
    return applied


async def memory_node(state: AssistantState) -> AssistantState:
    """Watchlist Q&A + mutations via the JSON action contract."""
    raw_user_id = state.get("user_id", "")
    try:
        user_id = uuid.UUID(raw_user_id)
    except ValueError:
        user_id = None
    watchlist = await _get_watchlist(user_id) if user_id else []
    alerts = await _get_alerts(user_id) if user_id else []
    system = SystemMessage(
        content=render_agent_prompt(
            "memory_system",
            user_name=state.get("user_name", "there"),
            watchlist=", ".join(watchlist) or "(empty)",
            alerts=", ".join(alerts) or "(none)",
        )
    )
    try:
        done = await stream(
            [system, UserMessage(content=state["user_msg"])],
            temperature=0.3,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        parsed = parse_reply(done.text)
    except Exception as exc:  # noqa: BLE001
        log.error("node.memory.error", error=str(exc))
        return {
            "final_text": "I couldn't update your watchlist right now — please try again.",
            "route": "memory",
        }
    actions = parsed.get("actions") or []
    applied = await _apply_actions(user_id, actions) if user_id and actions else []
    message = str(parsed.get("message", "Done."))
    emit_frame({"type": "token", "delta": message})
    return {"final_text": message, "route": "memory", "actions": applied}
