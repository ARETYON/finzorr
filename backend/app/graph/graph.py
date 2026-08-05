"""The assistant graph: supervisor -> one specialist -> persist.

Checkpointer: AsyncPostgresSaver keyed by thread_id=session_id (conversation
context survives restarts). Its `.setup()` runs once lazily — a distinct
bootstrap from Alembic. If checkpointer init fails the graph compiles without
one and history degrades to a DB reload per turn instead of crashing.
"""

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.logging import log
from app.graph.nodes.general_chat import general_chat_node
from app.graph.nodes.memory import memory_node
from app.graph.nodes.nl2sql import nl2sql_node
from app.graph.nodes.persist import persist_node
from app.graph.nodes.rag import rag_node
from app.graph.nodes.tools import tools_node
from app.graph.nodes.web_search import web_search_node
from app.graph.state import AssistantState
from app.graph.supervisor import ROUTES, plan_and_route, route_selector

BRANCHES: dict[str, str] = {route: route for route in ROUTES}

_graph: Any = None
_has_checkpointer = False
_lock = asyncio.Lock()


def build_graph() -> StateGraph:
    """Wire nodes and edges (uncompiled — tests introspect this)."""
    builder = StateGraph(AssistantState)
    builder.add_node("supervisor", plan_and_route)
    builder.add_node("general_chat", general_chat_node)
    builder.add_node("memory", memory_node)
    builder.add_node("rag", rag_node)
    builder.add_node("web_search", web_search_node)
    builder.add_node("nl2sql", nl2sql_node)
    builder.add_node("tools", tools_node)
    builder.add_node("persist", persist_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_selector, BRANCHES)
    for route in ROUTES:
        builder.add_edge(route, "persist")
    builder.add_edge("persist", END)
    return builder


def _pg_dsn() -> str:
    """AsyncPostgresSaver uses psycopg — strip SQLAlchemy's driver marker."""
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def get_graph() -> tuple[Any, bool]:
    """Compiled-graph singleton; returns (graph, has_checkpointer)."""
    global _graph, _has_checkpointer  # noqa: PLW0603
    if _graph is not None:
        return _graph, _has_checkpointer
    async with _lock:
        if _graph is not None:
            return _graph, _has_checkpointer
        builder = build_graph()
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg import AsyncConnection

            conn = await AsyncConnection.connect(_pg_dsn(), autocommit=True)
            checkpointer = AsyncPostgresSaver(conn)  # type: ignore[arg-type]
            await checkpointer.setup()
            _graph = builder.compile(checkpointer=checkpointer)
            _has_checkpointer = True
            log.info("graph.compiled", checkpointer="postgres")
        except Exception as exc:  # noqa: BLE001 — degrade to stateless
            log.warning("graph.checkpointer.unavailable", error=str(exc))
            _graph = builder.compile()
            _has_checkpointer = False
        return _graph, _has_checkpointer
