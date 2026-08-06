"""Public share links + persona CRUD."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.chat_session import ChatSession
from app.models.message import Message
from app.models.persona import Persona
from app.models.share_token import ShareToken
from app.models.user import User

router = APIRouter(prefix="/api", tags=["sharing"])


@router.post("/chat/sessions/{session_id}/share", status_code=status.HTTP_201_CREATED)
async def create_share_link(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    token = ShareToken(session_id=session_id, user_id=user.id)
    db.add(token)
    await db.commit()
    return {"token": str(token.id)}


@router.get("/share/{token}")
async def view_shared(token: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """Public read-only transcript (no auth by design)."""
    share = await db.get(ShareToken, token)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link not found")
    session = await db.get(ChatSession, share.session_id)
    result = await db.execute(
        select(Message).where(Message.session_id == share.session_id).order_by(Message.created_at)
    )
    return {
        "title": session.title if session else "Shared chat",
        "messages": [
            {"role": m.role, "content": m.content, "route": m.route} for m in result.scalars()
        ],
    }


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    system_prompt: str = Field(min_length=1, max_length=4000)


@router.get("/personas")
async def list_personas(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, str]]:
    result = await db.execute(
        select(Persona).where(Persona.user_id == user.id).order_by(Persona.created_at)
    )
    return [
        {"id": str(p.id), "name": p.name, "system_prompt": p.system_prompt}
        for p in result.scalars()
    ]


@router.post("/personas", status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: PersonaIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    persona = Persona(user_id=user.id, name=body.name, system_prompt=body.system_prompt)
    db.add(persona)
    await db.commit()
    return {"id": str(persona.id)}


@router.delete("/personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(
    persona_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(
        delete(Persona).where(Persona.id == persona_id, Persona.user_id == user.id)
    )
    await db.commit()


class SessionPersonaIn(BaseModel):
    persona_id: uuid.UUID | None


@router.patch("/chat/sessions/{session_id}/persona")
async def set_session_persona(
    session_id: uuid.UUID,
    body: SessionPersonaIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    if body.persona_id is not None:
        persona = await db.get(Persona, body.persona_id)
        if persona is None or persona.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    session.persona_id = body.persona_id
    await db.commit()
    return {"persona_id": str(body.persona_id) if body.persona_id else None}
