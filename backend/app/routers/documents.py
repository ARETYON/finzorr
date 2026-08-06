"""Document upload/list/delete endpoints (validated, user-scoped)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.core.logging import log
from app.core.pagination import BARE_LIST_DESCRIPTION, Page, page_params
from app.core.rate_limit import check_rate_limit
from app.db.session import get_db
from app.documents.ingest import (
    DocumentTooLargeError,
    UnsupportedDocumentError,
    ingest_document,
)
from app.documents.storage import get_storage
from app.models.document import Document
from app.models.user import User
from app.rag.vector_store import delete_document as qdrant_delete_document
from app.schemas.documents import DocumentOut, DocumentUploadOut

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOWED_EXTENSIONS = (".pdf", ".docx", ".csv", ".txt", ".md")
_MAGIC = {".pdf": b"%PDF-", ".docx": b"PK"}
_READ_CHUNK = 1024 * 1024


def _validate_type(filename: str, data: bytes) -> str | None:
    """Return an error message, or None when the file looks legitimate."""
    lower = filename.lower()
    if not lower.endswith(_ALLOWED_EXTENSIONS):
        return "supported types: PDF, DOCX, CSV, TXT, MD"
    for ext, magic in _MAGIC.items():
        if lower.endswith(ext) and not data.startswith(magic):
            return f"file does not look like a valid {ext[1:].upper()}"
    return None


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks, aborting the moment it exceeds the cap —
    never buffer an arbitrarily large body just to measure it."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=DocumentUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentUploadOut:
    """Validate (rate, type, magic bytes, size, per-user cap), store, ingest."""
    if not await check_rate_limit(f"upload:{user.id}"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "upload limit reached — please wait"
        )
    count = await db.scalar(
        select(func.count()).select_from(Document).where(Document.user_id == user.id)
    )
    if (count or 0) >= settings.MAX_DOCS_PER_USER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"document limit reached ({settings.MAX_DOCS_PER_USER})"
        )
    data = await _read_capped(file, settings.MAX_UPLOAD_MB * 1024 * 1024)
    type_error = _validate_type(file.filename or "", data)
    if type_error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, type_error)

    doc = Document(
        user_id=user.id,
        filename=file.filename or "document",
        storage_key="",
        status="pending",
    )
    db.add(doc)
    await db.flush()
    doc.storage_key = f"{user.id}/{doc.id}-{doc.filename}"
    storage_key = doc.storage_key
    await get_storage().save(storage_key, data)
    try:
        chunk_count = await ingest_document(user.id, doc.id, doc.filename, data)
        doc.status = "ready"
        doc.chunk_count = chunk_count
    except (DocumentTooLargeError, UnsupportedDocumentError) as exc:
        # the row rolls back — the blob must go too, or it's orphaned forever
        await db.rollback()
        try:
            await get_storage().delete(storage_key)
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            log.warning("document.orphan_blob", storage_key=storage_key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — record failure, keep the row
        log.error("document.ingest_failed", error=str(exc))
        doc.status = "failed"
    await db.commit()
    return DocumentUploadOut(id=doc.id, status=doc.status, chunks=doc.chunk_count or 0)


@router.get("", response_model=list[DocumentOut], description=BARE_LIST_DESCRIPTION)
async def list_documents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> list[DocumentOut]:
    result = await db.execute(
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.uploaded_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return [
        DocumentOut(
            id=d.id,
            filename=d.filename,
            status=d.status,
            chunks=d.chunk_count,
            uploaded_at=d.uploaded_at,
        )
        for d in result.scalars()
    ]


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    # remove chunks for every batch-suffixed doc_id prefix, the blob, then the row
    for start in range(0, (doc.chunk_count or 0) + 16, 16):
        await qdrant_delete_document(str(user.id), f"{doc.id}:{start}")
    await get_storage().delete(doc.storage_key)
    await db.delete(doc)
    await db.commit()
