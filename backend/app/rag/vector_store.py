"""Qdrant wrapper — one collection, tenant-partitioned payloads.

Tenants: "glossary" (global corpus) and per-user UUIDs (uploaded documents).
Filtered ANN only ever touches the caller's slice plus the global corpus.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.core.config import settings
from app.core.logging import log

COLLECTION = "knowledge"
GLOSSARY_TENANT = "glossary"

_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    global _client  # noqa: PLW0603 — lazy singleton
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None
        )
    return _client


async def ensure_collection() -> None:
    """Create the collection + tenant payload index if missing (idempotent)."""
    client = get_client()
    if not await client.collection_exists(COLLECTION):
        await client.create_collection(
            COLLECTION,
            vectors_config=models.VectorParams(
                size=settings.EMBED_DIM, distance=models.Distance.COSINE
            ),
        )
        await client.create_payload_index(
            COLLECTION, field_name="tenant", field_schema=models.PayloadSchemaType.KEYWORD
        )
        await client.create_payload_index(
            COLLECTION, field_name="doc_id", field_schema=models.PayloadSchemaType.KEYWORD
        )
        log.info("qdrant.collection_created", collection=COLLECTION)


def stable_id(tenant: str, doc_id: str, index: int) -> str:
    """Deterministic point id — re-ingest overwrites instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant}/{doc_id}/{index}"))


@dataclass
class Hit:
    score: float
    text: str
    title: str
    locator: str  # e.g. "p.4" or "glossary"
    tenant: str


async def upsert_chunks(
    tenant: str,
    doc_id: str,
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
) -> int:
    """Upsert chunk payloads with stable ids; returns the count."""
    await ensure_collection()
    points = [
        models.PointStruct(
            id=stable_id(tenant, doc_id, i),
            vector=vector,
            payload={"tenant": tenant, "doc_id": doc_id, **chunk},
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    await get_client().upsert(COLLECTION, points=points)
    return len(points)


async def search(
    vector: list[float], tenants: list[str], top_k: int = 6, min_score: float = 0.4
) -> list[Hit]:
    """Tenant-filtered ANN search."""
    await ensure_collection()
    response = await get_client().query_points(
        COLLECTION,
        query=vector,
        limit=top_k,
        score_threshold=min_score,
        query_filter=models.Filter(
            must=[models.FieldCondition(key="tenant", match=models.MatchAny(any=tenants))]
        ),
    )
    return [
        Hit(
            score=float(p.score),
            text=str((p.payload or {}).get("text", "")),
            title=str((p.payload or {}).get("title", "")),
            locator=str((p.payload or {}).get("locator", "")),
            tenant=str((p.payload or {}).get("tenant", "")),
        )
        for p in response.points
    ]


async def delete_document(tenant: str, doc_id: str) -> None:
    """Remove every chunk of one document (re-ingest/delete path)."""
    await get_client().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="tenant", match=models.MatchValue(value=tenant)),
                    models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
                ]
            )
        ),
    )


async def delete_tenant(tenant: str) -> None:
    """Right-to-erasure: remove a user's entire slice."""
    await get_client().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="tenant", match=models.MatchValue(value=tenant))]
            )
        ),
    )
