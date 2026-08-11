"""Glossary corpus ingestion (maintainer-run, idempotent).

Run: `uv run python -m app.rag.ingest_corpus`

Parses `seed_corpus/glossary.md` ("## Term | category" + body), one chunk per
entry (entries are already atomic — no windowed chunking needed), embeds via
Ollama, and upserts into the "glossary" tenant with stable ids.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import configure_logging, log
from app.infrastructure.vector_store import GLOSSARY_TENANT, upsert_chunks
from app.rag.embeddings import embed_texts

_CORPUS = Path(__file__).parent / "seed_corpus" / "glossary.md"
_ENTRY = re.compile(r"^## (?P<term>.+?) \| (?P<category>\w+)\s*$")
_BATCH = 16


@dataclass
class Entry:
    term: str
    category: str
    body: str


def parse_corpus(text: str) -> list[Entry]:
    """Split the markdown into atomic entries."""
    entries: list[Entry] = []
    current: Entry | None = None
    for line in text.splitlines():
        match = _ENTRY.match(line)
        if match:
            current = Entry(match["term"].strip(), match["category"].strip(), "")
            entries.append(current)
        elif current is not None and line.strip() and not line.startswith("#"):
            current.body = f"{current.body} {line.strip()}".strip()
    return [e for e in entries if e.body]


async def ingest() -> int:
    """Embed and upsert every entry; returns the count."""
    entries = parse_corpus(_CORPUS.read_text())
    total = 0
    for start in range(0, len(entries), _BATCH):
        batch = entries[start : start + _BATCH]
        vectors = await embed_texts([f"{e.term}: {e.body}" for e in batch])
        chunks = [
            {
                "text": e.body,
                "title": e.term,
                "locator": "glossary",
                "category": e.category,
            }
            for e in batch
        ]
        # doc_id fixed per entry position-window so re-runs overwrite cleanly
        total += await upsert_chunks(GLOSSARY_TENANT, f"glossary-{start}", chunks, vectors)
    log.info("corpus.ingested", entries=total)
    return total


def main() -> None:
    configure_logging()
    count = asyncio.run(ingest())
    log.info("corpus.done", count=count)


if __name__ == "__main__":
    main()
