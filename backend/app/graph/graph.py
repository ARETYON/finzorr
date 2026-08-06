"""The assistant graph: supervisor plans -> specialists execute -> persist.

Shape: supervisor emits an executable 1-3 step plan; each specialist edge
lands on `advance`, which records the step's output and either arms the next
specialist (feeding prior outputs forward), synthesizes via `compose`
(multi-step only), or persists. The tools route is its own checkpointed
plan⇄exec loop inside a step.

Checkpointer: AsyncPostgresSaver keyed by thread_id=session_id (conversation
context survives restarts). It runs over an AsyncConnectionPool — a single
AsyncConnection would serialize every user's checkpoint I/O behind one lock
and, if Postgres dropped it, fail permanently until restart. The pool checks
connections on checkout and reconnects on its own. `.setup()` runs once lazily
— a distinct bootstrap from Alembic. If checkpointer init fails the graph
compiles without one and history degrades to a DB reload per turn instead of
crashing.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Hashable
from functools import wraps
from typing import Any, cast

from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy, RetryPolicy
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.config import settings
from app.core.logging import log
from app.core.otel import span
from app.graph.nodes.advance import advance_node, after_step
from app.graph.nodes.compose import compose_node
from app.graph.nodes.general_chat import general_chat_node
from app.graph.nodes.memory import memory_node
from app.graph.nodes.nl2sql import nl2sql_node
from app.graph.nodes.parallel import join_node, spec_runner_node
from app.graph.nodes.persist import persist_node
from app.graph.nodes.rag import rag_node
from app.graph.nodes.replan import replan_node
from app.graph.nodes.research import (
    research_plan_node,
    research_read_node,
    research_search_node,
    research_synthesize_node,
)
from app.graph.nodes.tools import tools_exec_node, tools_next, tools_plan_node
from app.graph.nodes.web_search import web_search_node
from app.graph.state import AssistantState
from app.graph.supervisor import ROUTES, plan_and_route, route_selector

# The "tools" route enters the checkpointed plan⇄exec loop at tools_plan.
_ROUTE_ENTRY = {"tools": "tools_plan", "research": "research_plan"}
BRANCHES: dict[Hashable, str] = {
    route: _ROUTE_ENTRY.get(route, route) for route in ROUTES
}

_graph: Any = None
_has_checkpointer = False
_pool: Any = None
_store: Any = None
_lock = asyncio.Lock()

# Small pool: checkpoint I/O is short-lived; the dev/prod VM is deliberately
# tiny, so cap connections rather than scale with users. Bumped 4→6 when the
# LangGraph store (memory search, on every turn's hot path) joined the pool.
_POOL_MIN, _POOL_MAX = 1, 6

# Degraded (checkpointer-less) mode is a state, not a life sentence: retry the
# attach this often. Previously it was decided once at first request forever.
_DEGRADED_RETRY_S = 30.0
_degraded_retry_at = 0.0

def traced[NodeF: Callable[..., Awaitable[Any]]](name: str, fn: NodeF) -> NodeF:
    """Every node becomes a span — the graph is invisible to traces otherwise."""

    @wraps(fn)
    async def wrapper(state: AssistantState) -> Any:
        with span("node", node=name):
            return await fn(state)

    return cast(NodeF, wrapper)


def _research_search_key(state: dict[str, Any]) -> bytes:
    """Cache key for research_search: the sub-questions ONLY. The default
    key pickles the whole node input (turn_id, messages, ...) and would
    never hit twice. Deliberately cross-user: the node's output is public
    web results, no user data involved."""
    import json

    return json.dumps(sorted(state.get("research_subs", [])), sort_keys=True).encode()


# Same query re-researched within 5 minutes skips the search fan-out.
_SEARCH_CACHE = CachePolicy(ttl=300, key_func=_research_search_key)

# Transient DB failures retry once at graph level before persist degrades.
_PERSIST_RETRY = RetryPolicy(max_attempts=2, retry_on=(OperationalError, InterfaceError))


def build_graph() -> StateGraph[AssistantState]:
    """Wire nodes and edges (uncompiled — tests introspect this)."""
    builder = StateGraph(AssistantState)
    builder.add_node("supervisor", traced("supervisor", plan_and_route))
    builder.add_node("general_chat", traced("general_chat", general_chat_node))
    builder.add_node("memory", traced("memory", memory_node))
    builder.add_node("rag", traced("rag", rag_node))
    builder.add_node("web_search", traced("web_search", web_search_node))
    builder.add_node("nl2sql", traced("nl2sql", nl2sql_node))
    builder.add_node("tools_plan", traced("tools_plan", tools_plan_node))
    builder.add_node("tools_exec", traced("tools_exec", tools_exec_node))
    builder.add_node("research_plan", traced("research_plan", research_plan_node))
    builder.add_node(
        "research_search",
        traced("research_search", research_search_node),
        cache_policy=_SEARCH_CACHE,
    )
    builder.add_node("research_read", traced("research_read", research_read_node))
    builder.add_node(
        "research_synthesize", traced("research_synthesize", research_synthesize_node)
    )
    builder.add_node("advance", traced("advance", advance_node))
    builder.add_node("replan", traced("replan", replan_node))
    builder.add_node("spec_runner", spec_runner_node)  # spans itself per-branch
    builder.add_node("join", traced("join", join_node))
    builder.add_node("compose", traced("compose", compose_node))
    builder.add_node("persist", traced("persist", persist_node), retry_policy=_PERSIST_RETRY)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_selector, BRANCHES)
    # every specialist reports back to the plan walker
    for route in ROUTES - {"tools", "research"}:
        builder.add_edge(route, "advance")
    # the research pipeline: four checkpointed stages
    builder.add_edge("research_plan", "research_search")
    builder.add_edge("research_search", "research_read")
    builder.add_edge("research_read", "research_synthesize")
    builder.add_edge("research_synthesize", "advance")
    # the tools agent loop: plan -> (exec -> plan)* -> advance
    builder.add_conditional_edges(
        "tools_plan", tools_next, {"tools_exec": "tools_exec", "advance": "advance"}
    )
    builder.add_edge("tools_exec", "tools_plan")
    # advance: next plan step | compose (multi-step) | persist
    builder.add_conditional_edges(
        "advance",
        after_step,
        {**BRANCHES, "replan": "replan", "compose": "compose", "persist": "persist"},
    )
    # replan re-enters the plan walk (revised step) or exits honestly
    builder.add_conditional_edges(
        "replan", after_step, {**BRANCHES, "compose": "compose", "persist": "persist"}
    )
    # parallel fan-out barrier: all Send branches -> join -> compose
    builder.add_edge("spec_runner", "join")
    builder.add_edge("join", "compose")
    builder.add_edge("compose", "persist")
    builder.add_edge("persist", END)
    return builder


def _pg_dsn() -> str:
    """AsyncPostgresSaver uses psycopg — strip SQLAlchemy's driver marker."""
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def get_graph() -> tuple[Any, bool]:
    """Compiled-graph singleton; returns (graph, has_checkpointer).

    A degraded (checkpointer-less) compile is served from cache but
    re-attempted every _DEGRADED_RETRY_S — a Postgres blip at boot must not
    condemn the process to statelessness for its whole life.
    """
    global _graph, _has_checkpointer, _pool, _store, _degraded_retry_at  # noqa: PLW0603
    now = asyncio.get_running_loop().time()
    if _graph is not None and (_has_checkpointer or now < _degraded_retry_at):
        return _graph, _has_checkpointer
    async with _lock:
        now = asyncio.get_running_loop().time()
        if _graph is not None and (_has_checkpointer or now < _degraded_retry_at):
            return _graph, _has_checkpointer
        builder = build_graph()
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            pool: Any = AsyncConnectionPool(
                _pg_dsn(),
                min_size=_POOL_MIN,
                max_size=_POOL_MAX,
                open=False,
                check=AsyncConnectionPool.check_connection,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
            )
            await pool.open(wait=True, timeout=10)
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            store: Any = None
            try:
                # BaseStore-backed long-term memory: shares the pool (same
                # autocommit/dict_row kwargs the store expects); the semantic
                # index needs pgvector — on plain Postgres, setup() fails and
                # memory degrades to the legacy Qdrant path, never to "off".
                from langgraph.store.postgres import AsyncPostgresStore

                from app.memory.store_embed import store_index

                store = AsyncPostgresStore(pool, index=cast(Any, store_index()))
                await store.setup()
                log.info("graph.store", backend="postgres-pgvector")
            except Exception as store_exc:  # noqa: BLE001 — Qdrant fallback covers memory
                log.warning("graph.store.unavailable", error=str(store_exc))
                store = None
            _pool = pool
            _store = store
            if store is not None:
                _graph = builder.compile(
                    checkpointer=checkpointer, cache=InMemoryCache(), store=store
                )
            else:
                _graph = builder.compile(checkpointer=checkpointer, cache=InMemoryCache())
            _has_checkpointer = True
            log.info("graph.compiled", checkpointer="postgres-pool", pool_max=_POOL_MAX)
        except Exception as exc:  # noqa: BLE001 — degrade to stateless, retry later
            log.warning("graph.checkpointer.unavailable", error=str(exc))
            if "pool" in locals():
                with contextlib.suppress(Exception):
                    await pool.close()  # half-open pool must not leak
            _graph = builder.compile(cache=InMemoryCache())
            _has_checkpointer = False
            _degraded_retry_at = asyncio.get_running_loop().time() + _DEGRADED_RETRY_S
        return _graph, _has_checkpointer


def get_store() -> Any:
    """The LangGraph BaseStore when available (None => Qdrant fallback)."""
    return _store


async def close_graph() -> None:
    """Release the store batch task + checkpointer pool on shutdown."""
    global _graph, _has_checkpointer, _pool, _store  # noqa: PLW0603
    if _store is not None:
        # AsyncBatchedBaseStore spawns a background batch task that only
        # __del__ would cancel — do it deterministically here.
        task = getattr(_store, "_task", None)
        if task is not None:
            task.cancel()
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise
            log.warning("graph.pool.close_failed", error=str(exc))
    _graph, _has_checkpointer, _pool, _store = None, False, None, None
