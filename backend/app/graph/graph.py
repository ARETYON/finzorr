"""The assistant graph: supervisor -> one specialist -> persist.

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
from collections.abc import Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.logging import log
from app.graph.nodes.general_chat import general_chat_node
from app.graph.nodes.memory import memory_node
from app.graph.nodes.nl2sql import nl2sql_node
from app.graph.nodes.persist import persist_node
from app.graph.nodes.rag import rag_node
from app.graph.nodes.tools import tools_exec_node, tools_next, tools_plan_node
from app.graph.nodes.web_search import web_search_node
from app.graph.state import AssistantState
from app.graph.supervisor import ROUTES, plan_and_route, route_selector

# The "tools" route enters the checkpointed plan⇄exec loop at tools_plan.
BRANCHES: dict[Hashable, str] = {
    route: ("tools_plan" if route == "tools" else route) for route in ROUTES
}

_graph: Any = None
_has_checkpointer = False
_pool: Any = None
_lock = asyncio.Lock()

# Small pool: checkpoint I/O is short-lived; the dev/prod VM is deliberately
# tiny, so cap connections rather than scale with users.
_POOL_MIN, _POOL_MAX = 1, 4


def build_graph() -> StateGraph[AssistantState]:
    """Wire nodes and edges (uncompiled — tests introspect this)."""
    builder = StateGraph(AssistantState)
    builder.add_node("supervisor", plan_and_route)
    builder.add_node("general_chat", general_chat_node)
    builder.add_node("memory", memory_node)
    builder.add_node("rag", rag_node)
    builder.add_node("web_search", web_search_node)
    builder.add_node("nl2sql", nl2sql_node)
    builder.add_node("tools_plan", tools_plan_node)
    builder.add_node("tools_exec", tools_exec_node)
    builder.add_node("persist", persist_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_selector, BRANCHES)
    for route in ROUTES - {"tools"}:
        builder.add_edge(route, "persist")
    # the agent loop: plan -> (exec -> plan)* -> persist
    builder.add_conditional_edges(
        "tools_plan", tools_next, {"tools_exec": "tools_exec", "persist": "persist"}
    )
    builder.add_edge("tools_exec", "tools_plan")
    builder.add_edge("persist", END)
    return builder


def _pg_dsn() -> str:
    """AsyncPostgresSaver uses psycopg — strip SQLAlchemy's driver marker."""
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def get_graph() -> tuple[Any, bool]:
    """Compiled-graph singleton; returns (graph, has_checkpointer)."""
    global _graph, _has_checkpointer, _pool  # noqa: PLW0603
    if _graph is not None:
        return _graph, _has_checkpointer
    async with _lock:
        if _graph is not None:
            return _graph, _has_checkpointer
        builder = build_graph()
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            _pool = AsyncConnectionPool(
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
            await _pool.open(wait=True, timeout=10)
            checkpointer = AsyncPostgresSaver(_pool)
            await checkpointer.setup()
            _graph = builder.compile(checkpointer=checkpointer)
            _has_checkpointer = True
            log.info("graph.compiled", checkpointer="postgres-pool", pool_max=_POOL_MAX)
        except Exception as exc:  # noqa: BLE001 — degrade to stateless
            log.warning("graph.checkpointer.unavailable", error=str(exc))
            _graph = builder.compile()
            _has_checkpointer = False
        return _graph, _has_checkpointer


async def close_graph() -> None:
    """Release the checkpointer pool on shutdown (no-op when degraded)."""
    global _graph, _has_checkpointer, _pool  # noqa: PLW0603
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise
            log.warning("graph.pool.close_failed", error=str(exc))
    _graph, _has_checkpointer, _pool = None, False, None
