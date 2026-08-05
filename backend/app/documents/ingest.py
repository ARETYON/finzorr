"""PDF -> chunks -> embeddings -> Qdrant (per-user tenant).

Extraction is PyMuPDF text (digital PDFs; OCR is a Phase-2 addition).
Chunks are locator-aware: every chunk carries its page anchor so RAG answers
can cite `file.pdf · p.N`.
"""

import asyncio
import uuid

import fitz  # PyMuPDF

from app.core.config import settings
from app.core.logging import log
from app.rag.embeddings import embed_texts
from app.rag.vector_store import upsert_chunks

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
_EMBED_BATCH = 16


class DocumentTooLargeError(Exception):
    """Raised when a PDF exceeds the configured page cap."""


def extract_pages(pdf_bytes: bytes) -> list[str]:
    """Extract text per page; raises DocumentTooLargeError over the page cap."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count > settings.MAX_UPLOAD_PAGES:
            raise DocumentTooLargeError(
                f"{doc.page_count} pages exceeds the {settings.MAX_UPLOAD_PAGES}-page limit"
            )
        return [page.get_text() for page in doc]


def chunk_pages(pages: list[str], filename: str) -> list[dict[str, str]]:
    """Window each page's text, keeping the page number as the citation anchor."""
    chunks: list[dict[str, str]] = []
    for page_num, text in enumerate(pages, start=1):
        clean = " ".join(text.split())
        if not clean:
            continue
        start = 0
        while start < len(clean):
            piece = clean[start : start + CHUNK_CHARS]
            chunks.append({"text": piece, "title": filename, "locator": f"p.{page_num}"})
            if start + CHUNK_CHARS >= len(clean):
                break
            start += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks


async def ingest_pdf(user_id: uuid.UUID, doc_id: uuid.UUID, filename: str, pdf: bytes) -> int:
    """Extract, chunk, embed, and upsert one uploaded PDF. Returns chunk count."""
    pages = await asyncio.to_thread(extract_pages, pdf)
    chunks = chunk_pages(pages, filename)
    if not chunks:
        return 0
    total = 0
    for start in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[start : start + _EMBED_BATCH]
        vectors = await embed_texts([c["text"] for c in batch])
        # stable ids need a per-batch offset baked into doc_id ordering
        total += await upsert_chunks(str(user_id), f"{doc_id}:{start}", batch, vectors)
    log.info("document.ingested", doc_id=str(doc_id), chunks=total, pages=len(pages))
    return total
