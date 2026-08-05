"""Document upload/list/delete endpoints (PDF-only, validated, user-scoped)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.core.logging import log
from app.db.session import get_db
from app.documents.ingest import DocumentTooLargeError, ingest_pdf
from app.documents.storage import get_storage
from app.models.document import Document
from app.models.user import User
from app.rag.vector_store import delete_document as qdrant_delete_document

router = APIRouter(prefix="/api/documents", tags=["documents"])

_PDF_MAGIC = b"%PDF-"


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """Validate (type, magic bytes, size, per-user cap), store, and ingest."""
    count = await db.scalar(
        select(func.count()).select_from(Document).where(Document.user_id == user.id)
    )
    if (count or 0) >= settings.MAX_DOCS_PER_USER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"document limit reached ({settings.MAX_DOCS_PER_USER})"
        )
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file exceeds size limit")
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PDF files are supported")

    doc = Document(
        user_id=user.id,
        filename=file.filename or "document.pdf",
        storage_key="",
        status="pending",
    )
    db.add(doc)
    await db.flush()
    doc.storage_key = f"{user.id}/{doc.id}.pdf"
    await get_storage().save(doc.storage_key, data)
    try:
        chunk_count = await ingest_pdf(user.id, doc.id, doc.filename, data)
        doc.status = "ready"
        doc.chunk_count = chunk_count
    except DocumentTooLargeError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — record failure, keep the row
        log.error("document.ingest_failed", error=str(exc))
        doc.status = "failed"
    await db.commit()
    return {"id": str(doc.id), "status": doc.status, "chunks": doc.chunk_count or 0}


@router.get("")
async def list_documents(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, str | int | None]]:
    result = await db.execute(
        select(Document).where(Document.user_id == user.id).order_by(Document.uploaded_at.desc())
    )
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "status": d.status,
            "chunks": d.chunk_count,
            "uploaded_at": d.uploaded_at.isoformat(),
        }
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
