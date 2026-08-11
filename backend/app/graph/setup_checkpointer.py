"""One-off schema setup for the LangGraph checkpointer + store, run ONCE
before the multi-worker api service starts (same pattern as `alembic
upgrade head`) — never lazily from inside a running worker.

Root cause this fixes: `get_graph()`'s lazy `checkpointer.setup()` is
guarded by an in-process `asyncio.Lock`, which does nothing across the
SEPARATE OS processes `--workers 2` spawns. Both workers' first request
raced to run `CREATE INDEX CONCURRENTLY IF NOT EXISTS
checkpoints_thread_id_idx`, Postgres serialized the two, and any
concurrently-idle transaction (e.g. an unrelated `/chat/sessions` request
landing on the same wedged worker) blocked the CONCURRENTLY build from
ever completing — a live deadlock, not a slow query. Running this once,
single-process, before workers start means every worker's first
`get_graph()` call finds the schema already present and skips setup().

Usage: docker compose run --rm api python -m app.graph.setup_checkpointer
"""

import asyncio
from typing import Any, cast

from app.core.logging import log
from app.graph.graph import _pg_dsn  # noqa: SLF001 — intentional reuse, not a public API


async def _setup() -> None:
    from psycopg_pool import AsyncConnectionPool

    pool: Any = AsyncConnectionPool(_pg_dsn(), min_size=1, max_size=1, open=False)
    await pool.open(wait=True, timeout=30)
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        log.info("setup_checkpointer.checkpointer_ready")

        try:
            from langgraph.store.postgres import AsyncPostgresStore

            from app.memory.store_embed import store_index

            store = AsyncPostgresStore(pool, index=cast(Any, store_index()))
            await store.setup()
            log.info("setup_checkpointer.store_ready")
        except Exception as exc:  # noqa: BLE001 — store needs pgvector; degrades to Qdrant at runtime
            log.warning("setup_checkpointer.store_unavailable", error=str(exc))
    finally:
        await pool.close()


def main() -> None:
    asyncio.run(_setup())


if __name__ == "__main__":
    main()
