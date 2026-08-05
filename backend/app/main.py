"""FastAPI application entrypoint.

Routers are registered here; heavy subsystems (graph, providers) are imported
lazily inside routers so a missing optional dependency never blocks startup.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, log
from app.routers import auth, chat, chat_ws, documents, health, market, watchlist


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, register optional tool families, log lifecycle."""
    configure_logging()
    log.info("app.startup", env=settings.APP_ENV)
    # Optional tool families — absence of keys/config is graceful, never fatal.
    try:
        from app.mcp_client.github_client import register_github_tools

        await register_github_tools()
    except Exception as exc:  # noqa: BLE001
        log.warning("startup.github_tools_failed", error=str(exc))
    try:
        from app.tools_registry.local_microservice import register_microservice_tools

        register_microservice_tools()
    except Exception as exc:  # noqa: BLE001
        log.warning("startup.microservice_tools_failed", error=str(exc))
    yield
    log.info("app.shutdown")


app = FastAPI(title="finzorr.ai API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(chat_ws.router)
app.include_router(market.router)
app.include_router(documents.router)
app.include_router(watchlist.router)

if settings.is_dev:
    from app.routers import debug

    app.include_router(debug.router)
