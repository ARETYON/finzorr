"""Chat image attachments (vision input). Stored per-user; token = filename."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.auth.dependencies import get_current_user
from app.documents.storage import get_storage
from app.models.user import User

router = APIRouter(prefix="/api/chat/attachments", tags=["attachments"])

_MAX_IMAGE_MB = 5
_MAGIC = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg"}


def mime_for(data: bytes) -> str | None:
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    return None


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile, user: User = Depends(get_current_user)
) -> dict[str, str]:
    """Accept a PNG/JPEG image for the next chat message."""
    data = await file.read()
    if len(data) > _MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "image exceeds 5MB")
    mime = mime_for(data)
    if mime is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PNG/JPEG images are supported")
    ext = "png" if mime == "image/png" else "jpg"
    token = f"{uuid.uuid4().hex}.{ext}"
    await get_storage().save(f"attachments/{user.id}/{token}", data)
    return {"token": token, "mime": mime}
