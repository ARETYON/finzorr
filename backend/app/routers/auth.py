"""Auth endpoints: Google login, dev bypass, logout, whoami."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_or_create_user
from app.auth.google_oauth import GoogleAuthError, verify_google_id_token
from app.auth.jwt_session import SESSION_COOKIE, cookie_kwargs, create_session_jwt
from app.core.config import settings
from app.core.logging import log
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import GoogleLoginIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


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


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    """Clear the session cookie."""
    response.delete_cookie(SESSION_COOKIE, domain=settings.COOKIE_DOMAIN or None)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user (401 if the cookie is missing/invalid)."""
    return user
