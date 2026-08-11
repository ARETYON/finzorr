"""Turn runner: one user message in, one persisted assistant reply out.

Now graph-backed: supervisor routing across all six specialists, Postgres
checkpointing (thread_id = session_id), and per-turn state reset so stale
citations/tool-calls never leak across turns. The WS layer's contract is
unchanged from the v1 single-node era.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.types import Overwrite
from langsmith import traceable
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import log, new_correlation_id
from app.core.otel import span
from app.core.tasks import spawn
from app.core.untrusted import wrap_untrusted
from app.graph.graph import get_graph
from app.graph.state import AssistantState
from app.infrastructure.db.session import SessionLocal
from app.models.message import Message
from app.models.user import User

HISTORY_RELOAD_LIMIT = 24  # degraded-mode (no checkpointer) history window


async def _load_history(session_id: uuid.UUID) -> list[dict[str, Any]]:
    """DB fallback history for the no-checkpointer degrade path."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(HISTORY_RELOAD_LIMIT)
        )
        rows = list(result.scalars())
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def _load_document_names(user_id: uuid.UUID) -> list[str]:
    """The user's READY upload filenames — routing awareness only. Without
    this, the planner can't know an uploaded report exists and a natural
    content question never reaches rag (found live)."""
    from app.models.document import Document

    async with SessionLocal() as db:
        result = await db.execute(
            select(Document.filename)
            .where(Document.user_id == user_id, Document.status == "ready")
            .order_by(Document.uploaded_at.desc())
            .limit(20)
        )
        return [row[0] for row in result]


async def _load_instructions(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Custom instructions + the session's persona prompt, merged."""
    from app.models.chat_session import ChatSession
    from app.models.persona import Persona

    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        parts = [(user.custom_instructions or "") if user else ""]
        session = await db.get(ChatSession, session_id)
        if session is not None and session.persona_id is not None:
            persona = await db.get(Persona, session.persona_id)
            if persona is not None:
                parts.append(f"Adopt this persona for every reply: {persona.system_prompt}")
    return "\n".join(p for p in parts if p).strip()


@traceable(run_type="chain", name="vision-turn")
async def _vision_turn(
    session_id: uuid.UUID, user_id: uuid.UUID, user_msg: str, attachments: list[str]
) -> dict[str, Any]:
    """Answer a question about an uploaded image, persisting the turn.
    Traceable: this path never enters the graph, so without the decorator an
    image turn produced NO trace at all."""
    from app.documents.storage import get_storage
    from app.infrastructure.llm.vision import describe_image, vision_available
    from app.specialists.persist import persist_node

    if not vision_available():
        answer = (
            "Image understanding isn't configured yet. Add GEMINI_API_KEY (free tier) "
            "or set VISION_MODEL to a local Ollama vision model (e.g. `ollama pull "
            "llava`) and restart."
        )
    else:
        try:
            token = attachments[0]
            data = await get_storage().load(f"attachments/{user_id}/{token}")
            mime = "image/png" if token.endswith(".png") else "image/jpeg"
            answer = await describe_image(user_msg, data, mime)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.error("vision.failed", error=str(exc))
            answer = "I couldn't analyze that image right now — please try again."
    from langsmith import get_current_run_tree

    vision_run = get_current_run_tree()
    out: AssistantState = {
        "session_id": str(session_id),
        "user_msg": f"{user_msg} [image attached]",
        "final_text": answer,
        "route": "general_chat",
        "ls_run_id": str(vision_run.id) if vision_run is not None else "",
    }
    persisted = await persist_node(out)
    return {
        "type": "response",
        "message_id": persisted.get("message_id", ""),
        "message": answer,
        "route": "general_chat",
        "route_reason": "image understanding",
        "citations": [],
        "tool_calls": [],
        "actions": [],
        "data_as_of": datetime.now(UTC).isoformat(),
        "sources": [],
        "chart": None,
        "session_id": str(session_id),
    }


OnFrame = Callable[[dict[str, Any]], Awaitable[None]]


@traceable(
    run_type="chain",
    name="turn.salvage",
    process_inputs=lambda inputs: {
        "session_id": str(inputs.get("session_id", "")),
        "turn_id": inputs.get("turn_id", ""),
        "reason": inputs.get("reason", ""),
        "text_len": len(inputs.get("final_text", "") or ""),
    },
)
async def record_out_of_band_turn(
    session_id: uuid.UUID,
    user_msg: str,
    final_text: str,
    turn_id: str = "",
    reason: str = "unknown",
) -> None:
    """Persist a turn that the graph did not finish (cancel/timeout) to BOTH
    stores: the DB (what the UI reloads) and the checkpointer thread (what
    the model remembers) — writing only one leaves a split-brain history.
    `turn_id` makes the write idempotent against a persist that already
    committed inside the graph before the cancel landed."""
    from app.core.trace import tag
    from app.specialists.persist import persist_node

    tag(f"salvage:{reason}")
    await persist_node(
        {
            "session_id": str(session_id),
            "user_msg": user_msg,
            "final_text": final_text,
            "turn_id": turn_id,
        }
    )
    try:
        graph, has_checkpointer = await get_graph()
        if has_checkpointer:
            config = {"configurable": {"thread_id": str(session_id)}}
            # as_node="persist": the write lands as if persist ran, so the
            # interrupted superstep can't resume stale on the next turn.
            await graph.aupdate_state(
                config,
                {
                    "messages": [
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": final_text},
                    ],
                    "pending_tool_calls": [],
                },
                as_node="persist",
            )
    except Exception as exc:  # noqa: BLE001 — DB write already succeeded
        log.warning("turn.checkpoint_sync_failed", error=str(exc))


async def run_turn(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    user_name: str,
    user_msg: str,
    attachments: list[str] | None = None,
    on_frame: OnFrame | None = None,
    turn_id: str = "",
    origin: str = "chat",
) -> dict[str, Any]:
    """Execute one turn through the graph; returns the WS response payload.

    `on_frame` receives streaming frames (thinking/token/tool_call) emitted
    by nodes through the graph's custom stream channel — scoped to THIS
    invocation, so concurrent turns can never cross streams. `turn_id` is the
    idempotency key shared with the caller's cancel path.
    """
    cid = new_correlation_id()
    turn_id = turn_id or uuid.uuid4().hex
    # pre-assigned so the root trace id is known BEFORE the run — persisted
    # on the Message row, it lets user feedback attach to the exact trace
    root_run_id = uuid.uuid4()
    # observe-only screening (BEFORE the vision branch so image turns are
    # screened too): a suspicious verdict tags the trace and logs — it never
    # blocks and never changes what the model sees
    from app.core.guard import screen_message

    guard_verdict = await screen_message(user_msg)
    if attachments:
        return await _vision_turn(session_id, user_id, user_msg, attachments)
    graph, has_checkpointer = await get_graph()
    user_instructions = await _load_instructions(user_id, session_id)
    user_documents = await _load_document_names(user_id)
    from app.memory.facts import extract_and_store, recall

    memories = await recall(
        str(user_id),
        user_msg,
        langsmith_extra={"metadata": {"session_id": str(session_id), "turn_id": turn_id}},
    )
    if memories:
        # Recalled facts were LLM-extracted from past user text — they are
        # personalization DATA, never instructions. Without this framing a
        # user could "remember" a directive once and have it land in the
        # always-obey slot of every future system prompt.
        memory_note = wrap_untrusted(
            "\n".join(f"- {m}" for m in memories), "recalled user memory"
        )
        user_instructions = f"{user_instructions}\n{memory_note}".strip()
    turn_input: AssistantState = {
        "session_id": str(session_id),
        "user_id": str(user_id),
        "user_name": user_name,
        "user_msg": user_msg,
        "user_documents": user_documents,
        "ls_run_id": str(root_run_id),
        "correlation_id": cid,
        "turn_id": turn_id,
        # explicit per-turn resets — only `messages` may accumulate
        "route": "",
        "plan_steps": [],
        "plan_index": 0,
        "step_outputs": [],
        "current_task": "",
        "route_reason": "",
        "step_error": False,
        "needs_replan": False,
        "replan_count": 0,
        "plan_parallel": False,
        "parallel_branch": False,
        "parallel_outputs": cast("list[dict[str, Any]]", Overwrite([])),
        "final_text": "",
        "citations": [],
        "tool_calls": [],
        "actions": [],
        "data_as_of": "",
        "sources": [],
        "message_id": "",
        "chart": {},
        "tool_transcript": [],
        "pending_tool_calls": [],
        "tool_iterations": 0,
        "research_subs": [],
        "research_sources": [],
        "research_pages": [],
        "hitl_enabled": has_checkpointer,
        "user_instructions": user_instructions,
    }
    if not has_checkpointer:
        turn_input["messages"] = await _load_history(session_id)
    config = {"configurable": {"thread_id": str(session_id)}}
    if has_checkpointer:
        await _clear_parked(graph, config, session_id)
    try:
        out = await _drive_graph(
            graph,
            turn_input,
            config,
            session_id,
            on_frame,
            origin=origin,
            user_id=str(user_id),
            turn_id=turn_id,
            run_id=root_run_id,
            extra_tags=["guard:suspicious"] if guard_verdict == "suspicious" else [],
        )
    except TimeoutError:
        return await _timeout_payload(session_id, user_msg, turn_id)
    if payload := _interrupt_payload(session_id, out):
        return payload
    log.info("turn.done", session_id=str(session_id), route=out.get("route"))
    spawn(
        extract_and_store(str(user_id), user_msg, out.get("final_text", "")),
        name="memory.extract",
    )
    return _response_payload(session_id, out)


async def get_parked_approval(session_id: uuid.UUID) -> dict[str, Any] | None:
    """The pending HITL interrupt on this thread, if any — lets a reloaded
    client re-discover an approval banner instead of orphaning the turn."""
    graph, has_checkpointer = await get_graph()
    if not has_checkpointer:
        return None
    config = {"configurable": {"thread_id": str(session_id)}}
    try:
        state = await graph.aget_state(config)
    except Exception:  # noqa: BLE001 — no thread yet
        return None
    for task in getattr(state, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            value = getattr(intr, "value", None)
            if isinstance(value, dict) and value.get("tools"):
                return {"tools": value["tools"], "user_msg": state.values.get("user_msg", "")}
    return None


async def _clear_parked(graph: Any, config: dict[str, Any], session_id: uuid.UUID) -> None:
    """A NEW message on a thread with a parked interrupt abandons the parked
    turn cleanly — otherwise the pending task lingers on the thread forever.
    The abandoned exchange is persisted so the user's message isn't lost."""
    try:
        state = await graph.aget_state(config)
        if not getattr(state, "next", ()):
            return
        parked_msg = str(state.values.get("user_msg", ""))
        log.info("turn.parked_abandoned", session_id=str(session_id))
        if parked_msg:
            # BOTH stores — the parked user message must survive in the DB
            # transcript too, not only in the model's memory
            await record_out_of_band_turn(
                session_id,
                parked_msg,
                "_(approval request superseded by a new message)_",
                turn_id=str(state.values.get("turn_id", "")),
                reason="parked_abandoned",
            )
        else:
            await graph.aupdate_state(config, {"pending_tool_calls": []}, as_node="persist")
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        log.warning("turn.clear_parked_failed", error=str(exc))


async def resume_turn(
    session_id: uuid.UUID,
    approved: bool,
    on_frame: OnFrame | None = None,
) -> dict[str, Any]:
    """Resume a turn parked at a HITL interrupt with the user's decision."""
    from langgraph.types import Command

    graph, has_checkpointer = await get_graph()
    if not has_checkpointer:
        return _error_payload(session_id, "nothing to approve — no parked turn")
    # A stale approval frame must NOT replay the previous answer — langgraph
    # happily resumes a thread with no pending interrupt to its last state.
    parked = await get_parked_approval(session_id)
    if parked is None:
        return _error_payload(session_id, "nothing to approve — no parked turn")
    parked_user_msg = str(parked.get("user_msg", "")) or "(approval decision)"
    config = {"configurable": {"thread_id": str(session_id)}}
    try:
        out = await _drive_graph(
            graph, Command(resume=approved), config, session_id, on_frame, origin="resume"
        )
    except TimeoutError:
        return await _timeout_payload(session_id, parked_user_msg, uuid.uuid4().hex)
    except Exception as exc:  # noqa: BLE001
        # a REAL mid-resume failure must not masquerade as "nothing to
        # approve" — the park was verified above, so this is an execution
        # error and the user should retry the approval
        log.error("turn.resume_failed", session_id=str(session_id), error=str(exc))
        return _error_payload(
            session_id, "the approval could not be processed — please retry"
        )
    if payload := _interrupt_payload(session_id, out):
        return payload  # a later step interrupted again
    log.info("turn.resumed", session_id=str(session_id), approved=approved)
    from app.memory.facts import extract_and_store

    spawn(
        extract_and_store(
            str(out.get("user_id", "")), parked_user_msg, out.get("final_text", "")
        ),
        name="memory.extract",
    )
    return _response_payload(session_id, out)


async def _drive_graph(
    graph: Any,
    graph_input: Any,
    config: dict[str, Any],
    session_id: uuid.UUID,
    on_frame: OnFrame | None,
    *,
    origin: str = "chat",
    user_id: str = "",
    turn_id: str = "",
    run_id: uuid.UUID | None = None,
    extra_tags: list[str] | None = None,
) -> AssistantState:
    """Stream one graph invocation under the per-turn deadline."""
    out: AssistantState = {}
    # explicit recursion ceiling: langgraph 1.x's default (10007) is no
    # backstop; a 3-step plan with full tool loops fits comfortably in 50.
    # run_name/metadata/tags surface in LangSmith when tracing is enabled —
    # a resumed turn is a separate root trace by design, so session_id is
    # what stitches a conversation's traces together there.
    # origin tag separates human chat / scheduled / resumed traffic in
    # LangSmith; user_id + turn_id make traces joinable with structured logs
    metadata = {"session_id": str(session_id)}
    if user_id:
        metadata["user_id"] = user_id
    if turn_id:
        metadata["turn_id"] = turn_id
    run_config = {
        **config,
        "recursion_limit": 50,
        "run_name": "assistant-turn",
        "metadata": metadata,
        "tags": [settings.APP_ENV, origin, *(extra_tags or [])],
    }
    if run_id is not None:
        run_config["run_id"] = run_id  # honored by Pregel — the root trace id
    async with asyncio.timeout(settings.TURN_TIMEOUT_S):
        with span("turn", session_id=str(session_id)) as turn_span:
            async for mode, chunk in graph.astream(
                graph_input, config=run_config, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    if on_frame is not None:
                        await on_frame(dict(chunk))
                else:
                    out = chunk
            turn_span.set_attribute("route", out.get("route", ""))
            turn_span.set_attribute("tool_calls", len(out.get("tool_calls", [])))
    return out


def _interrupt_payload(
    session_id: uuid.UUID, out: AssistantState
) -> dict[str, Any] | None:
    """The approval_required frame when the graph parked at an interrupt."""
    interrupts = dict(out).get("__interrupt__") if isinstance(out, dict) else None
    if not isinstance(interrupts, list | tuple) or not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", first)
    tools = value.get("tools", []) if isinstance(value, dict) else []
    log.info("turn.interrupted", session_id=str(session_id), tools=len(tools))
    return {
        "type": "approval_required",
        "tools": tools,
        "session_id": str(session_id),
    }


async def _timeout_payload(
    session_id: uuid.UUID, user_msg: str, turn_id: str
) -> dict[str, Any]:
    log.error("turn.timeout", session_id=str(session_id), limit_s=settings.TURN_TIMEOUT_S)
    note = (
        "This request took longer than the per-turn limit and was stopped — "
        "please try a narrower question."
    )
    await record_out_of_band_turn(
        session_id, user_msg, note, turn_id=turn_id, reason="timeout"
    )
    payload = _response_payload(session_id, {"final_text": note})
    payload["route_reason"] = "turn timeout"
    return payload


def _error_payload(session_id: uuid.UUID, message: str) -> dict[str, Any]:
    return {"type": "error", "message": message, "session_id": str(session_id)}


def _response_payload(session_id: uuid.UUID, out: AssistantState) -> dict[str, Any]:
    return {
        "type": "response",
        "message_id": out.get("message_id", ""),
        "message": out.get("final_text", ""),
        "route": out.get("route", "general_chat"),
        "route_reason": out.get("route_reason", ""),
        "citations": out.get("citations", []),
        "tool_calls": out.get("tool_calls", []),
        "actions": out.get("actions", []),
        "data_as_of": out.get("data_as_of") or datetime.now(UTC).isoformat(),
        "sources": out.get("sources", []),
        "chart": out.get("chart") or None,
        "session_id": str(session_id),
    }
