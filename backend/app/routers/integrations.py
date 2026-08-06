"""Google connector OAuth endpoints (gated on GOOGLE_CLIENT_SECRET)."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from app.auth.dependencies import get_current_user
from app.auth.jwt_session import create_session_jwt, verify_session_jwt
from app.core.config import settings
from app.integrations.google_connect import authorize_url, connectors_enabled, exchange_code
from app.models.user import User

router = APIRouter(prefix="/integrations/google", tags=["integrations"])


@router.get("/authorize")
async def google_authorize(user: User = Depends(get_current_user)) -> RedirectResponse:
    """Start the connector consent flow (state = short-lived session JWT)."""
    if not connectors_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connectors not configured")
    return RedirectResponse(authorize_url(state=create_session_jwt(user.id)))


@router.get("/callback")
async def google_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    """OAuth redirect target; verifies state and stores tokens."""
    if error or not code:
        return RedirectResponse(f"{settings.FRONTEND_ORIGIN}/chat?google=denied")
    user_id = verify_session_jwt(state)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad state")
    await exchange_code(code, user_id)
    return RedirectResponse(f"{settings.FRONTEND_ORIGIN}/chat?google=connected")
