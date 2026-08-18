"""Gmail/Calendar/Drive connectors — the Phase-2 auth upgrade, gated on secret.

Full OAuth code-exchange flow (offline access, incremental consent) with the
refresh token Fernet-encrypted at rest. Tools register only when
GOOGLE_CLIENT_SECRET is configured; users connect from Settings. All scopes
are read-only (LLM08 excessive-agency guard). Drive was added after
Gmail/Calendar: tokens granted before then lack its scope, so the Drive
handlers check the per-user stored scopes and ask for a reconnect instead of
failing with an opaque 403.
"""

import base64
import hashlib
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.logging import log
from app.core.request_context import get_current_user_id
from app.core.untrusted import wrap_untrusted
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.llm.base import ToolDefinition
from app.models.oauth_token import OAuthToken
from app.tools_registry.dispatcher import register_tool

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
_TIMEOUT_S = 20.0
_NOT_CONNECTED = (
    "Google is not connected for this account — open Settings and click "
    "'Connect Google' first."
)
_DRIVE_NOT_GRANTED = (
    "Error: Google Drive isn't enabled for this account yet — open Settings "
    "and reconnect Google to grant Drive access."
)
_DRIVE_READ_MAX_CHARS = 6000


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SESSION_SECRET.encode()).digest())
    return Fernet(key)


def connectors_enabled() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.OAUTH_REDIRECT_URL,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, user_id: uuid.UUID) -> None:
    """Trade the auth code for tokens; store the refresh token encrypted."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.OAUTH_REDIRECT_URL,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise ValueError("no refresh_token returned (was consent granted?)")
    # store the ACTUALLY granted scopes (Google's granular-consent screen lets
    # users untick individual scopes) so per-user capability checks are truthful
    granted_scopes = str(payload.get("scope", " ".join(SCOPES)))
    encrypted = _fernet().encrypt(refresh_token.encode()).decode()
    async with SessionLocal() as db:
        row = await db.get(OAuthToken, user_id)
        if row is None:
            db.add(
                OAuthToken(
                    user_id=user_id, refresh_token_enc=encrypted, scopes=granted_scopes
                )
            )
        else:
            row.refresh_token_enc = encrypted
            row.scopes = granted_scopes
        await db.commit()
    log.info("google.connected", user_id=str(user_id))


async def _access_token(user_id: uuid.UUID) -> str | None:
    async with SessionLocal() as db:
        row = await db.get(OAuthToken, user_id)
    if row is None:
        return None
    refresh_token = _fernet().decrypt(row.refresh_token_enc.encode()).decode()
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.post(
            _TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        return str(response.json()["access_token"])


async def _google_get(path: str, params: dict[str, Any]) -> dict[str, Any] | str:
    raw_user = get_current_user_id()
    try:
        user_id = uuid.UUID(raw_user)
    except ValueError:
        return "Error: no user context."
    token = await _access_token(user_id)
    if token is None:
        return _NOT_CONNECTED
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.get(
            f"https://www.googleapis.com{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


def _has_drive_scope(scopes: str) -> bool:
    return "drive.readonly" in scopes


async def _user_scopes(user_id: uuid.UUID) -> str | None:
    async with SessionLocal() as db:
        row = await db.get(OAuthToken, user_id)
    return None if row is None else row.scopes


async def _drive_precheck() -> tuple[uuid.UUID, None] | tuple[None, str]:
    """Resolve the user and confirm their token carries the Drive scope."""
    raw_user = get_current_user_id()
    try:
        user_id = uuid.UUID(raw_user)
    except ValueError:
        return None, "Error: no user context."
    scopes = await _user_scopes(user_id)
    if scopes is None:
        return None, _NOT_CONNECTED
    if not _has_drive_scope(scopes):
        return None, _DRIVE_NOT_GRANTED
    return user_id, None


async def _google_get_text(user_id: uuid.UUID, path: str, params: dict[str, Any]) -> str:
    """Raw-body variant of _google_get — Drive media/export return text, not JSON."""
    token = await _access_token(user_id)
    if token is None:
        return _NOT_CONNECTED
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.get(
            f"https://www.googleapis.com{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.text


def _escape_drive_query(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


async def _drive_search(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' is required."
    _, precheck_error = await _drive_precheck()
    if precheck_error is not None:
        return precheck_error
    safe = _escape_drive_query(query)
    listing = await _google_get(
        "/drive/v3/files",
        {
            "q": f"trashed=false and (name contains '{safe}' or fullText contains '{safe}')",
            "fields": "files(id,name,mimeType,modifiedTime)",
            "pageSize": 10,
        },
    )
    if isinstance(listing, str):
        return listing
    files = listing.get("files", [])
    if not files:
        return f"No Drive files matched: {query}"
    lines = [
        f"- [{f.get('id', '')}] {f.get('name', '')} "
        f"({f.get('mimeType', '')}, modified {f.get('modifiedTime', '')})"
        for f in files
    ]
    return wrap_untrusted("\n".join(lines), "drive file listing")


async def _drive_read(args: dict[str, Any]) -> str:
    file_id = str(args.get("file_id", "")).strip()
    if not file_id:
        return "Error: 'file_id' is required."
    user_id, precheck_error = await _drive_precheck()
    if precheck_error is not None or user_id is None:
        return precheck_error or _NOT_CONNECTED
    meta = await _google_get(f"/drive/v3/files/{file_id}", {"fields": "name,mimeType"})
    if isinstance(meta, str):
        return meta
    name = str(meta.get("name", file_id))
    mime = str(meta.get("mimeType", ""))
    if mime.startswith("application/vnd.google-apps"):
        export_mime = (
            "text/csv" if mime == "application/vnd.google-apps.spreadsheet" else "text/plain"
        )
        content = await _google_get_text(
            user_id, f"/drive/v3/files/{file_id}/export", {"mimeType": export_mime}
        )
    elif mime.startswith("text/") or mime in ("application/json", "application/xml"):
        content = await _google_get_text(user_id, f"/drive/v3/files/{file_id}", {"alt": "media"})
    else:
        return (
            f"Error: '{name}' has unsupported type '{mime}' — only Google Docs/"
            "Sheets and plain-text files can be read."
        )
    return wrap_untrusted(content[:_DRIVE_READ_MAX_CHARS], "drive file", header_extra=name)


async def _gmail_search(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip() or "in:inbox"
    listing = await _google_get(
        "/gmail/v1/users/me/messages", {"q": query, "maxResults": 5}
    )
    if isinstance(listing, str):
        return listing
    ids = [m["id"] for m in listing.get("messages", [])]
    if not ids:
        return f"No emails matched: {query}"
    lines = []
    for message_id in ids:
        detail = await _google_get(
            f"/gmail/v1/users/me/messages/{message_id}",
            {"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
        )
        if isinstance(detail, str):
            continue
        headers = {
            h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])
        }
        lines.append(
            f"- [{message_id}] {headers.get('Date','')} | {headers.get('From','')} | "
            f"{headers.get('Subject','(no subject)')} | {detail.get('snippet','')[:120]}"
        )
    return wrap_untrusted("\n".join(lines), "email content")


async def _calendar_events(args: dict[str, Any]) -> str:
    time_min = str(args.get("time_min", "")) or None
    params: dict[str, Any] = {"maxResults": 10, "singleEvents": True, "orderBy": "startTime"}
    if time_min:
        params["timeMin"] = time_min
    result = await _google_get("/calendar/v3/calendars/primary/events", params)
    if isinstance(result, str):
        return result
    events = result.get("items", [])
    if not events:
        return "No upcoming events found."
    return "\n".join(
        f"- {e.get('start', {}).get('dateTime', e.get('start', {}).get('date', ''))} | "
        f"{e.get('summary', '(no title)')}"
        for e in events
    )


def register_google_tools() -> int:
    """Register Gmail/Calendar/Drive tools only when the OAuth secret is configured."""
    if not connectors_enabled():
        return 0
    register_tool(
        ToolDefinition(
            name="gmail_search",
            description=(
                "Search the user's Gmail (read-only) with Gmail query syntax "
                "(e.g. 'from:foo subject:invoice newer_than:7d'). Returns headers + snippets."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _gmail_search,
    )
    register_tool(
        ToolDefinition(
            name="calendar_upcoming_events",
            description="List the user's next Google Calendar events (read-only).",
            input_schema={
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "RFC3339 start, default now"}
                },
            },
        ),
        _calendar_events,
    )
    register_tool(
        ToolDefinition(
            name="drive_search_files",
            description=(
                "Search the user's Google Drive (read-only) by file name or "
                "content keywords. Returns file id, name, type and modified time."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _drive_search,
    )
    register_tool(
        ToolDefinition(
            name="drive_read_file",
            description=(
                "Read a Google Drive file's text content (read-only) by file id "
                "(from drive_search_files). Google Docs/Sheets are exported as "
                "text/CSV; plain-text files read directly."
            ),
            input_schema={
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
                "required": ["file_id"],
            },
        ),
        _drive_read,
    )
    log.info("google.connectors_registered")
    return 4
