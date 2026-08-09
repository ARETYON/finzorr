"""Auth endpoints: Google login, dev bypass, logout, whoami."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_or_create_user
from app.auth.google_oauth import GoogleAuthError, verify_google_id_token
from app.auth.jwt_session import SESSION_COOKIE, cookie_kwargs, create_session_jwt
from app.core.config import settings
from app.core.guard import screen_floor
from app.core.logging import log
from app.core.pagination import BARE_LIST_DESCRIPTION
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import GoogleLoginIn, UserOut, UserUpdateIn
from app.schemas.misc import LogoutOut, MemoryFactOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session(response: Response, user: User) -> None:
    response.set_cookie(SESSION_COOKIE, create_session_jwt(user.id), **cookie_kwargs())  # type: ignore[arg-type]


@router.post("/google", response_model=UserOut)
async def google_login(
    body: GoogleLoginIn, response: Response, db: AsyncSession = Depends(get_db)
) -> User:
    """Verify a Google ID token, upsert the user, set the session cookie."""
    try:
        identity = verify_google_id_token(body.id_token)
    except GoogleAuthError as exc:
        log.warning("auth.google.rejected", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid Google credential") from exc
    user = await get_or_create_user(
        db,
        google_sub=identity.sub,
        email=identity.email,
        name=identity.name,
        picture=identity.picture,
    )
    _set_session(response, user)
    log.info("auth.login", user_id=str(user.id))
    return user


@router.post("/dev-login", response_model=UserOut)
async def dev_login(response: Response, db: AsyncSession = Depends(get_db)) -> User:
    """Dev-only bypass: fixed local user, honored ONLY when APP_ENV=dev."""
    if not (settings.is_dev and settings.DEV_FAKE_AUTH):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    user = await get_or_create_user(
        db,
        google_sub="dev-local-user",
        email="dev@localhost",
        name="Dev User",
        picture=None,
    )
    _set_session(response, user)
    return user


@router.post("/logout", response_model=LogoutOut)
async def logout(response: Response) -> LogoutOut:
    """Clear the session cookie."""
    response.delete_cookie(SESSION_COOKIE, domain=settings.COOKIE_DOMAIN or None)
    return LogoutOut(ok=True)


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UserUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Update per-user preferences (custom instructions)."""
    instructions = (body.custom_instructions or "").strip()[:2000]
    # write-time gate: a DIFFERENT risk profile than the runtime never-block
    # guard — this is gatekeeping a STORED, reusable artifact (every future
    # turn re-reads it) rather than a one-off message
    if instructions and screen_floor(instructions) == "suspicious":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "custom instructions can't contain override-style phrases "
            "(e.g. 'ignore previous instructions')",
        )
    merged = await db.merge(user)
    merged.custom_instructions = instructions or None
    await db.commit()
    await db.refresh(merged)
    return merged


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user (401 if the cookie is missing/invalid)."""
    return user


@router.get("/memories", response_model=list[MemoryFactOut], description=BARE_LIST_DESCRIPTION)
async def list_memories(user: User = Depends(get_current_user)) -> list[MemoryFactOut]:
    """The user's stored long-term memory facts."""
    from app.memory.facts import list_facts

    return [MemoryFactOut(**f) for f in await list_facts(str(user.id))]


@router.delete("/memories/{fact_id}", status_code=204)
async def delete_memory(fact_id: str, user: User = Depends(get_current_user)) -> None:
    """Delete one memory fact (user-scoped)."""
    from app.memory.facts import delete_fact

    await delete_fact(str(user.id), fact_id)
