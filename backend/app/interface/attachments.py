"""Chat image attachments (vision input). Stored per-user; token = filename."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.auth.dependencies import get_current_user
from app.documents.storage import get_storage
from app.infrastructure.rate_limit import check_rate_limit
from app.models.user import User
from app.schemas.misc import AttachmentUploadOut

router = APIRouter(prefix="/chat/attachments", tags=["attachments"])

_MAX_IMAGE_MB = 5
_MAGIC = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg"}
_READ_CHUNK = 1024 * 1024


def mime_for(data: bytes) -> str | None:
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    return None


@router.post("", response_model=AttachmentUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile, user: User = Depends(get_current_user)
) -> AttachmentUploadOut:
    """Accept a PNG/JPEG image for the next chat message."""
    if not await check_rate_limit(f"upload:{user.id}"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "upload limit reached — please wait"
        )
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > _MAX_IMAGE_MB * 1024 * 1024:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "image exceeds 5MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    mime = mime_for(data)
    if mime is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PNG/JPEG images are supported")
    ext = "png" if mime == "image/png" else "jpg"
    token = f"{uuid.uuid4().hex}.{ext}"
    await get_storage().save(f"attachments/{user.id}/{token}", data)
    return AttachmentUploadOut(token=token, mime=mime)


@router.get("/{token}")
async def get_attachment(token: str, user: User = Depends(get_current_user)) -> Response:
    """Serve one of the user's own attachments (uploaded or generated)."""
    if "/" in token or ".." in token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad token")
    try:
        data = await get_storage().load(f"attachments/{user.id}/{token}")
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from exc
    mime = "image/png" if token.endswith(".png") else "image/jpeg"
    return Response(content=data, media_type=mime)
