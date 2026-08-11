"""Integration: the REAL AsyncPostgresStore against the pgvector test DB.

Every other store test uses a fake — this one constructs the actual store
(setup, semantic index, namespace isolation, delete) with only the embedder
stubbed, so the pgvector path CI claims to exercise is actually exercised.
"""

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_DIMS = 768


def _vec(text: str) -> list[float]:
    """Deterministic one-hot embedding — identical text => identical vector."""
    v = [0.0] * _DIMS
    v[sum(ord(c) for c in text) % _DIMS] = 1.0
    return v


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [_vec(t) for t in texts]


async def test_real_store_put_search_delete(_database: None) -> None:
    from langgraph.store.postgres import AsyncPostgresStore
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from app.orchestration.graph import _pg_dsn

    pool: Any = AsyncConnectionPool(
        _pg_dsn(),
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=10)
    store: Any = None
    try:
        from typing import cast

        store = AsyncPostgresStore(
            pool, index=cast(Any, {"dims": _DIMS, "embed": _fake_embed})
        )
        await store.setup()

        run = uuid.uuid4().hex[:8]
        mine = ("memories", f"user-a-{run}")
        theirs = ("memories", f"user-b-{run}")
        await store.aput(mine, "k1", {"text": "prefers bullet points"})
        await store.aput(mine, "k2", {"text": "beginner investor"})
        await store.aput(theirs, "k3", {"text": "prefers bullet points"})

        # semantic search: identical text embeds identically => top hit
        hits = await store.asearch(mine, query="prefers bullet points", limit=2)
        assert hits and hits[0].value["text"] == "prefers bullet points"
        # namespace isolation is structural: user-b's rows never surface
        assert all(h.key in ("k1", "k2") for h in hits)

        listed = await store.asearch(mine, limit=10)
        assert {h.key for h in listed} == {"k1", "k2"}

        await store.adelete(mine, "k1")
        remaining = await store.asearch(mine, limit=10)
        assert {h.key for h in remaining} == {"k2"}
        # the other namespace is untouched by the delete
        other = await store.asearch(theirs, limit=10)
        assert {h.key for h in other} == {"k3"}
    finally:
        task = getattr(store, "_task", None)
        if task is not None:
            task.cancel()
        await pool.close()
