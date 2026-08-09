"""Session CRUD, message history, and feedback endpoints (all user-scoped).

Three routers: `router` (shared, mounted under /api AND /api/v1),
`legacy_router` (the offset lists, /api only), and `v1_router` (the same
lists with the cursor envelope, /api/v1 only) — separate routers so the
two list shapes never shadow each other in routing or OpenAPI.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.core.logging import log
from app.core.pagination import Page, page_params
from app.core.tasks import spawn
from app.db.session import get_db
from app.models.chat_session import ChatSession
from app.models.feedback import Feedback
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import (
    FeedbackIn,
    MessageOut,
    MessagePageOut,
    SessionOut,
    SessionPageOut,
    SessionRenameIn,
)
from app.schemas.misc import FeedbackCreateOut, PendingApprovalOut, SearchHitOut

router = APIRouter(prefix="/chat", tags=["chat"])
legacy_router = APIRouter(prefix="/chat", tags=["chat"])
v1_router = APIRouter(prefix="/chat", tags=["chat"])


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Parse `<isoformat>|<uuid>`; malformed cursors are a client error."""
    try:
        ts_raw, _, id_raw = cursor.partition("|")
        return datetime.fromisoformat(ts_raw), uuid.UUID(id_raw)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor"
        ) from exc


def _encode_cursor(ts: datetime, item_id: uuid.UUID) -> str:
    return f"{ts.isoformat()}|{item_id}"


async def _owned_session(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return session


@router.get("/search", response_model=list[SearchHitOut])
async def search_messages(
    q: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> list[SearchHitOut]:
    """Case-insensitive search over the user's own messages (trgm-indexed).

    The limit is clamped to 50 (below the shared 200 cap): every hit ships a
    content snippet, so pages are an order heavier than list rows. Search
    keeps the bare-list shape on v1 by design — it's an unbounded-relevance
    feed, not a stable collection, so cursor semantics don't fit it.
    """
    if not q.strip():
        return []
    result = await db.execute(
        select(Message, ChatSession.title)
        .join(ChatSession, Message.session_id == ChatSession.id)
        .where(ChatSession.user_id == user.id, Message.content.ilike(f"%{q}%"))
        .order_by(Message.created_at.desc())
        .limit(min(page.limit, 50))
        .offset(page.offset)
    )
    return [
        SearchHitOut(
            session_id=str(m.session_id),
            session_title=title or "New chat",
            role=m.role,
            snippet=m.content[:160],
            created_at=m.created_at.isoformat(),
        )
        for m, title in result.all()
    ]


@legacy_router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list(result.scalars())


@v1_router.get("/sessions", response_model=SessionPageOut)
async def list_sessions_v1(
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> SessionPageOut:
    """Keyset pagination on (updated_at DESC, id DESC).

    NOTE: the sort key is mutable — a session updated between pages moves
    to the front and can be seen twice (or skipped). Acceptable for a
    recency-ordered list; documented rather than hidden.
    """
    query = (
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .limit(page.limit + 1)  # +1 probes whether another page exists
    )
    if cursor is not None:
        ts, last_id = _decode_cursor(cursor)
        query = query.where(tuple_(ChatSession.updated_at, ChatSession.id) < (ts, last_id))
    rows = list((await db.execute(query)).scalars())
    has_more = len(rows) > page.limit
    rows = rows[: page.limit]
    total = (
        await db.execute(
            select(func.count())
            .select_from(ChatSession)
            .where(ChatSession.user_id == user.id)
        )
    ).scalar_one()
    return SessionPageOut(
        items=[SessionOut.model_validate(s) for s in rows],
        next_cursor=_encode_cursor(rows[-1].updated_at, rows[-1].id) if has_more else None,
        total=total,
    )


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> ChatSession:
    session = ChatSession(user_id=user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: uuid.UUID,
    body: SessionRenameIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatSession:
    session = await _owned_session(db, session_id, user)
    session.title = body.title
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _owned_session(db, session_id, user)
    await db.execute(delete(ChatSession).where(ChatSession.id == session_id))
    await db.commit()


@legacy_router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> list[Message]:
    """Newest-window semantics: the most recent `limit` messages, ascending."""
    await _owned_session(db, session_id, user)
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list(reversed(list(result.scalars())))


@v1_router.get("/sessions/{session_id}/messages", response_model=MessagePageOut)
async def list_messages_v1(
    session_id: uuid.UUID,
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> MessagePageOut:
    """Newest window first; `next_cursor` (created_at|id) pages toward OLDER
    messages — items are returned ascending for direct rendering either way.
    """
    await _owned_session(db, session_id, user)
    query = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(page.limit + 1)  # +1 probes whether another page exists
    )
    if cursor is not None:
        ts, last_id = _decode_cursor(cursor)
        query = query.where(tuple_(Message.created_at, Message.id) < (ts, last_id))
    rows = list((await db.execute(query)).scalars())
    has_more = len(rows) > page.limit
    rows = rows[: page.limit]
    total = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.session_id == session_id)
        )
    ).scalar_one()
    return MessagePageOut(
        items=[MessageOut.model_validate(m) for m in reversed(rows)],
        next_cursor=_encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None,
        total=total,
    )


@router.get("/sessions/{session_id}/pending-approval", response_model=PendingApprovalOut)
async def pending_approval(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PendingApprovalOut:
    """Re-discover a parked HITL approval after a page reload — without this
    a refresh mid-approval orphans the turn (the banner lived only in React
    state)."""
    await _owned_session(db, session_id, user)
    from app.graph.turn import get_parked_approval

    parked = await get_parked_approval(session_id)
    return PendingApprovalOut(
        pending=parked is not None, tools=(parked or {}).get("tools", [])
    )


@router.post(
    "/messages/{message_id}/feedback",
    response_model=FeedbackCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    message_id: uuid.UUID,
    body: FeedbackIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackCreateOut:
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    await _owned_session(db, message.session_id, user)
    # capture the preceding user query for eval-dataset seeding
    prev = await db.execute(
        select(Message)
        .where(
            Message.session_id == message.session_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    prev_user = prev.scalar_one_or_none()
    row = Feedback(
        message_id=message_id,
        session_id=message.session_id,
        route=message.route,
        query=prev_user.content if prev_user else "",
        response=message.content,
        citations=message.citations,
        rating=body.rating,
        comment=body.comment,
    )
    db.add(row)
    await db.commit()
    # Close the loop into LangSmith: attach the rating to the turn's exact
    # trace. Fire-and-forget, guarded, swallow-everything — a thumbs-down
    # must never 500, and out-of-band messages (ls_run_id NULL) no-op.
    if (
        settings.LANGSMITH_TRACING
        and settings.LANGSMITH_API_KEY
        and message.ls_run_id is not None
        and body.rating != 0
    ):
        spawn(
            _send_langsmith_feedback(
                message.ls_run_id, body.rating, body.comment, str(message_id)
            ),
            name="feedback.langsmith",
        )
    return FeedbackCreateOut(id=str(row.id))


async def _send_langsmith_feedback(
    run_id: uuid.UUID, rating: int, comment: str | None, message_id: str
) -> None:
    """Best-effort: run_id == trace_id for a root run, so the call batches."""
    try:
        from langsmith import Client

        Client().create_feedback(
            run_id=run_id,
            key="user_score",
            score=1 if rating > 0 else 0,
            comment=comment,
            trace_id=run_id,
            source_info={"message_id": message_id},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("feedback.langsmith_failed", error=str(exc))
