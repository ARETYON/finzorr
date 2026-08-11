"""Pure domain logic — no I/O, no framework dependencies. Safe to unit-test without mocks.

Text-chunking window over already-extracted labeled pseudo-pages, extracted
from `app/documents/ingest.py` (which does the actual extraction/embedding/
upsert I/O).
"""

LabeledPages = list[tuple[str, str]]  # (locator label, text)

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def chunk_pages(pages: LabeledPages, filename: str) -> list[dict[str, str]]:
    """Window each labeled pseudo-page, keeping its label as the citation
    anchor (`p.N` / `slide N` / `sheet:Name` / `§N`)."""
    chunks: list[dict[str, str]] = []
    for label, text in pages:
        clean = " ".join(text.split())
        if not clean:
            continue
        start = 0
        while start < len(clean):
            piece = clean[start : start + CHUNK_CHARS]
            chunks.append({"text": piece, "title": filename, "locator": label})
            if start + CHUNK_CHARS >= len(clean):
                break
            start += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks
