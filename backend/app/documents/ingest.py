"""Document -> chunks -> embeddings -> Qdrant (per-user tenant).

Supported: PDF (PyMuPDF), DOCX (python-docx), CSV/TXT (plain decode).
OCR for scanned PDFs is a Phase-2 addition. Chunks are locator-aware: every
chunk carries a page/section anchor so RAG answers can cite `file.pdf · p.N`.
"""

import asyncio
import csv as csv_module
import io
import uuid

import fitz  # PyMuPDF

from app.core.config import settings
from app.core.logging import log
from app.rag.embeddings import embed_texts
from app.rag.vector_store import upsert_chunks

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
_EMBED_BATCH = 16
_CSV_MAX_ROWS = 2000


class DocumentTooLargeError(Exception):
    """Raised when a document exceeds the configured page/size caps."""


class UnsupportedDocumentError(Exception):
    """Raised when a file type cannot be extracted."""


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


def extract_docx(data: bytes) -> list[str]:
    """Extract DOCX text as pseudo-pages (~40 paragraphs per section)."""
    import docx

    document = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            paragraphs.append(" | ".join(cell.text.strip() for cell in row.cells))
    if not paragraphs:
        return []
    section_size = 40
    return [
        "\n".join(paragraphs[i : i + section_size])
        for i in range(0, len(paragraphs), section_size)
    ]


def extract_text_file(data: bytes) -> list[str]:
    """Plain text/CSV decode as one pseudo-page (CSV rows joined readably)."""
    text = data.decode("utf-8", errors="replace")
    try:
        rows = list(csv_module.reader(io.StringIO(text)))
        if len(rows) > 1 and len(rows[0]) > 1:  # looks like a real CSV
            header = ", ".join(rows[0])
            lines = [f"Columns: {header}"] + [
                "; ".join(f"{h}={v}" for h, v in zip(rows[0], r, strict=False))
                for r in rows[1 : _CSV_MAX_ROWS + 1]
            ]
            text = "\n".join(lines)
    except csv_module.Error:
        pass
    return [text] if text.strip() else []


def extract_any(filename: str, data: bytes) -> list[str]:
    """Route extraction by extension; raises UnsupportedDocumentError otherwise."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_pages(data)
    if lower.endswith(".docx"):
        return extract_docx(data)
    if lower.endswith((".csv", ".txt", ".md")):
        return extract_text_file(data)
    raise UnsupportedDocumentError(f"unsupported file type: {filename}")


async def ingest_document(
    user_id: uuid.UUID, doc_id: uuid.UUID, filename: str, data: bytes
) -> int:
    """Extract (any supported type), chunk, embed, upsert. Returns chunk count."""
    pages = await asyncio.to_thread(extract_any, filename, data)
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
