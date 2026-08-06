"""ChatGPT-style personal memory.

After each turn a small fire-and-forget LLM call extracts durable facts about
the user ("beginner investor", "prefers Hindi") and stores them embedded in
Qdrant under tenant `memfacts:{user_id}`. At turn start the top-k facts
relevant to the new message are injected into system prompts. Users can list
and delete their memories (also the right-to-erasure path).
"""

import json
import re
import uuid
from typing import Any

from qdrant_client import models as qmodels

from app.ai.base import SystemMessage, UserMessage
from app.ai.completion import complete
from app.core.logging import log
from app.core.prompt_registry import AgentPrompt, register, render_agent_prompt
from app.rag.embeddings import embed_query, embed_texts
from app.rag.vector_store import COLLECTION, ensure_collection, get_client, search

MAX_FACTS_PER_TURN = 3
MAX_FACT_CHARS = 200  # a "fact" longer than this is a smuggled instruction, not a fact
RECALL_TOP_K = 4
RECALL_MIN_SCORE = 0.5
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)

register(
    AgentPrompt(
        name="memory_extractor",
        version="1",
        template=(
            "Extract durable personal facts about the user from this exchange — "
            "preferences, expertise level, goals, constraints. Ignore one-off "
            "questions, market data, and anything transient.\n"
            'Reply ONLY a JSON array of short fact strings (max {max_facts}), or [] '
            "if nothing durable.\nUser: {user_msg}\nAssistant: {reply}"
        ),
    )
)


def tenant_for(user_id: str) -> str:
    return f"memfacts:{user_id}"


def shape_facts(raw_facts: list[Any]) -> list[str]:
    """Single-line + length-capped: extracted facts come from arbitrary user
    text and are later placed in system prompts, so they must stay
    fact-shaped — never multi-line directive blocks."""
    facts = [" ".join(str(f).split())[:MAX_FACT_CHARS] for f in raw_facts]
    return [f for f in facts if f][:MAX_FACTS_PER_TURN]


def _store() -> "Any | None":
    """LangGraph BaseStore when the pgvector index is up; None => Qdrant."""
    from app.graph.graph import get_store

    return get_store()


_NAMESPACE = "memories"


async def extract_and_store(user_id: str, user_msg: str, reply: str) -> int:
    """Fire-and-forget worker: extract facts and upsert them. Returns count."""
    try:
        raw = await complete(
            [
                SystemMessage(content="You extract durable user facts as JSON."),
                UserMessage(
                    content=render_agent_prompt(
                        "memory_extractor",
                        max_facts=str(MAX_FACTS_PER_TURN),
                        user_msg=user_msg[:800],
                        reply=reply[:800],
                    )
                ),
            ],
            temperature=0.0,
            max_tokens=200,
        )
        match = _JSON_ARRAY.search(raw)
        facts = shape_facts(json.loads(match.group(0)) if match else [])
        if not facts:
            return 0
        store = _store()
        if store is not None:
            # BaseStore path: namespaced put; the store's pgvector index
            # embeds `text` itself (same 768-dim Ollama embedder)
            for fact in facts:
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user_id}/fact/{fact.lower()}"))
                await store.aput((_NAMESPACE, user_id), key, {"text": fact})
            log.info("memory.stored", user_id=user_id, facts=len(facts), backend="store")
            return len(facts)
        vectors = await embed_texts(facts)
        await ensure_collection()
        points = [
            qmodels.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user_id}/fact/{fact.lower()}")),
                vector=vector,
                payload={
                    "tenant": tenant_for(user_id),
                    "doc_id": "memfact",
                    "text": fact,
                    "title": "memory",
                    "locator": "memory",
                },
            )
            for fact, vector in zip(facts, vectors, strict=True)
        ]
        await get_client().upsert(COLLECTION, points=points)
        log.info("memory.stored", user_id=user_id, facts=len(points))
        return len(points)
    except Exception as exc:  # noqa: BLE001 — memory is best-effort
        log.warning("memory.extract_failed", error=str(exc))
        return 0


async def recall(user_id: str, query: str) -> list[str]:
    """Top-k facts relevant to the incoming message (best-effort)."""
    try:
        store = _store()
        if store is not None:
            hits = await store.asearch((_NAMESPACE, user_id), query=query, limit=RECALL_TOP_K)
            return [
                str(item.value.get("text", ""))
                for item in hits
                if item.score is None or item.score >= RECALL_MIN_SCORE
            ]
        vector = await embed_query(query)
        hits = await search(
            vector, tenants=[tenant_for(user_id)], top_k=RECALL_TOP_K, min_score=RECALL_MIN_SCORE
        )
        return [h.text for h in hits]
    except Exception:  # noqa: BLE001
        return []


async def list_facts(user_id: str) -> list[dict[str, str]]:
    """Every stored fact for the settings UI."""
    store = _store()
    if store is not None:
        items = await store.asearch((_NAMESPACE, user_id), limit=200)
        return [{"id": item.key, "text": str(item.value.get("text", ""))} for item in items]
    await ensure_collection()
    points, _ = await get_client().scroll(
        COLLECTION,
        scroll_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="tenant", match=qmodels.MatchValue(value=tenant_for(user_id))
                )
            ]
        ),
        limit=200,
        with_payload=True,
    )
    return [{"id": str(p.id), "text": str((p.payload or {}).get("text", ""))} for p in points]


async def delete_fact(user_id: str, point_id: str) -> None:
    """Delete one fact — only if it belongs to this user's tenant."""
    store = _store()
    if store is not None:
        # namespace ownership is structural: the delete is scoped to this
        # user's namespace, so another user's key can't be reached
        await store.adelete((_NAMESPACE, user_id), point_id)
        return
    facts = await list_facts(user_id)
    if any(f["id"] == point_id for f in facts):
        await get_client().delete(
            COLLECTION, points_selector=qmodels.PointIdsList(points=[point_id])
        )
