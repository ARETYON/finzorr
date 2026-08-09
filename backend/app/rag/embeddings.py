"""Embeddings via Ollama's batch /api/embed endpoint.

Same model (`nomic-embed-text:v1.5`, 768-dim) in every environment — dev uses
the host's native Ollama, uat/prod use the ollama-embed container — so the
vector space never drifts between environments.
"""

import httpx
from langsmith import traceable

from app.core.config import settings
from app.core.pii import redact_for_trace

_TIMEOUT_S = 30.0


class EmbeddingError(Exception):
    """Raised when the embedding service fails or returns a bad shape."""


def _trace_preview(inputs: dict[str, object]) -> dict[str, object]:
    """Shape only, plus a PII-redacted preview — never raw document text
    in a trace payload."""
    texts = inputs.get("texts") or [""]
    first = texts[0] if isinstance(texts, list) and texts else ""
    return {
        "count": len(texts) if isinstance(texts, list) else 0,
        "first": redact_for_trace(str(first))[:120],
    }


@traceable(run_type="embedding", name="embed", process_inputs=_trace_preview)
async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts; raises EmbeddingError on any failure."""
    if not texts:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.post(
                f"{settings.EMBED_OLLAMA_URL}/api/embed",
                json={"model": settings.EMBED_MODEL, "input": texts},
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise EmbeddingError(f"embedding request failed: {exc}") from exc
    if len(embeddings) != len(texts):
        raise EmbeddingError(f"expected {len(texts)} embeddings, got {len(embeddings)}")
    return [[float(x) for x in vector] for vector in embeddings]


async def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return (await embed_texts([text]))[0]
