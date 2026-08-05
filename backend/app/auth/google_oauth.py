"""Google Identity Services ID-token verification.

The frontend obtains an ID token from the GIS button; we verify signature,
audience and expiry against Google's public certs. No client secret needed —
this app never calls Google APIs on the user's behalf (that's the Phase 2
Gmail upgrade, which would switch to the full code-exchange flow).
"""

from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings


@dataclass
class GoogleIdentity:
    """The verified claims we care about."""

    sub: str
    email: str
    name: str
    picture: str | None


class GoogleAuthError(Exception):
    """Raised when an ID token fails verification."""


def verify_google_id_token(token: str) -> GoogleIdentity:
    """Verify a Google ID token and return its identity claims.

    Raises GoogleAuthError on any failure (bad signature, wrong audience,
    expired, or GOOGLE_CLIENT_ID unset).
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise GoogleAuthError("GOOGLE_CLIENT_ID is not configured")
    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as exc:
        raise GoogleAuthError(f"invalid Google ID token: {exc}") from exc
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(claims.get("email", "")),
        name=str(claims.get("name", claims.get("email", "user"))),
        picture=claims.get("picture"),
    )
