"""Index config for the LangGraph store's semantic search.

Wraps the existing Ollama embedding client (768-dim nomic-embed-text) as the
async embed function the store's pgvector index calls on put/search.
"""

from typing import Any


async def _embed(texts: list[str]) -> list[list[float]]:
    from app.rag.embeddings import embed_texts

    return await embed_texts(texts)


def store_index() -> dict[str, Any]:
    return {"dims": 768, "embed": _embed}
