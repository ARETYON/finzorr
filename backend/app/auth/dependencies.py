"""FastAPI dependencies for the authenticated user (HTTP and WS variants)."""

import uuid

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_session import SESSION_COOKIE, verify_session_jwt
from app.infrastructure.db.session import SessionLocal, get_db
from app.models.user import User


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Resolve the session cookie to a User or raise 401."""
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = verify_session_jwt(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return user


async def get_ws_user(websocket: WebSocket) -> User | None:
    """WS handshake auth: read the cookie off the scope manually.

    Returns None (caller closes with 4401) instead of raising — `Depends()`
    injection behaves differently for WS routes.
    """
    token = websocket.cookies.get(SESSION_COOKIE, "")
    user_id = verify_session_jwt(token) if token else None
    if user_id is None:
        return None
    async with SessionLocal() as db:
        return await db.get(User, user_id)


async def get_or_create_user(
    db: AsyncSession, *, google_sub: str, email: str, name: str, picture: str | None
) -> User:
    """Upsert a user by google_sub, updating profile fields on every login."""
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid.uuid4(), google_sub=google_sub, email=email, name=name, picture_url=picture
        )
        db.add(user)
    else:
        user.email = email
        user.name = name
        user.picture_url = picture
        from app.models.user import utcnow

        user.last_login_at = utcnow()
    await db.commit()
    await db.refresh(user)
    return user
