"""Public share links (with expiry + revocation) + persona CRUD."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.core.guard import screen_floor
from app.core.pagination import BARE_LIST_DESCRIPTION, Page, page_params
from app.db.session import get_db
from app.models.chat_session import ChatSession
from app.models.message import Message
from app.models.persona import Persona
from app.models.share_token import ShareToken
from app.models.user import User, utcnow
from app.schemas.sharing import (
    PersonaCreateOut,
    PersonaIn,
    PersonaOut,
    SessionPersonaIn,
    SessionPersonaOut,
    ShareCreateOut,
    SharedChatOut,
    SharedMessageOut,
)

router = APIRouter(prefix="", tags=["sharing"])

SHARE_VIEW_MAX_MESSAGES = 200


async def _owned_session(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return session


@router.post(
    "/chat/sessions/{session_id}/share",
    response_model=ShareCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_share_link(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareCreateOut:
    await _owned_session(db, session_id, user)
    expires_at = (
        utcnow() + timedelta(days=settings.SHARE_TTL_DAYS)
        if settings.SHARE_TTL_DAYS > 0
        else None
    )
    token = ShareToken(session_id=session_id, user_id=user.id, expires_at=expires_at)
    db.add(token)
    await db.commit()
    return ShareCreateOut(token=str(token.id), expires_at=expires_at)


@router.delete("/chat/sessions/{session_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_links(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke every share link for a session (a leaked URL dies here)."""
    await _owned_session(db, session_id, user)
    result = await db.execute(
        delete(ShareToken).where(
            ShareToken.session_id == session_id, ShareToken.user_id == user.id
        )
    )
    await db.commit()
    if getattr(result, "rowcount", 0) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no share links for this session")


@router.get("/share/{token}", response_model=SharedChatOut)
async def view_shared(token: uuid.UUID, db: AsyncSession = Depends(get_db)) -> SharedChatOut:
    """Public read-only transcript (no auth by design; capped + expiring)."""
    share = await db.get(ShareToken, token)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link not found")
    if share.expires_at is not None and share.expires_at < utcnow():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link expired")
    session = await db.get(ChatSession, share.session_id)
    result = await db.execute(
        select(Message)
        .where(Message.session_id == share.session_id)
        .order_by(Message.created_at.desc())
        .limit(SHARE_VIEW_MAX_MESSAGES)
    )
    messages = list(reversed(list(result.scalars())))
    return SharedChatOut(
        title=(session.title if session else None) or "Shared chat",
        messages=[
            SharedMessageOut(role=m.role, content=m.content, route=m.route) for m in messages
        ],
    )


@router.get("/personas", response_model=list[PersonaOut], description=BARE_LIST_DESCRIPTION)
async def list_personas(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> list[Persona]:
    result = await db.execute(
        select(Persona)
        .where(Persona.user_id == user.id)
        .order_by(Persona.created_at)
        .limit(page.limit)
        .offset(page.offset)
    )
    return list(result.scalars())


@router.post("/personas", response_model=PersonaCreateOut, status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: PersonaIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PersonaCreateOut:
    # write-time gate: a persona's system_prompt is a STORED, reusable
    # artifact (re-read into every future turn using it) — a different
    # risk profile than the runtime never-block guard
    if screen_floor(body.system_prompt) == "suspicious":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "persona instructions can't contain override-style phrases "
            "(e.g. 'ignore previous instructions')",
        )
    persona = Persona(user_id=user.id, name=body.name, system_prompt=body.system_prompt)
    db.add(persona)
    await db.commit()
    return PersonaCreateOut(id=persona.id)


@router.delete("/personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(
    persona_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        delete(Persona).where(Persona.id == persona_id, Persona.user_id == user.id)
    )
    await db.commit()
    if getattr(result, "rowcount", 0) == 0:  # not found OR not owned — 404 like everywhere
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")


@router.patch("/chat/sessions/{session_id}/persona", response_model=SessionPersonaOut)
async def set_session_persona(
    session_id: uuid.UUID,
    body: SessionPersonaIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionPersonaOut:
    session = await _owned_session(db, session_id, user)
    if body.persona_id is not None:
        persona = await db.get(Persona, body.persona_id)
        if persona is None or persona.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    session.persona_id = body.persona_id
    await db.commit()
    return SessionPersonaOut(persona_id=body.persona_id)
