"""Session CRUD, message history, and feedback endpoints (all user-scoped)."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.pagination import Page, page_params
from app.db.session import get_db
from app.models.chat_session import ChatSession
from app.models.feedback import Feedback
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import FeedbackIn, MessageOut, SessionOut, SessionRenameIn
from app.schemas.misc import FeedbackCreateOut, SearchHitOut

router = APIRouter(prefix="/api/chat", tags=["chat"])


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
    """Case-insensitive search over the user's own messages (trgm-indexed)."""
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


@router.get("/sessions", response_model=list[SessionOut])
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


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
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


@router.get("/sessions/{session_id}/pending-approval")
async def pending_approval(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-discover a parked HITL approval after a page reload — without this
    a refresh mid-approval orphans the turn (the banner lived only in React
    state)."""
    await _owned_session(db, session_id, user)
    from app.graph.turn import get_parked_approval

    parked = await get_parked_approval(session_id)
    return {"pending": parked is not None, "tools": (parked or {}).get("tools", [])}


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
    return FeedbackCreateOut(id=str(row.id))
