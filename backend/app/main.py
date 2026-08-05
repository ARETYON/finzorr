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
from app.routers import auth, chat, chat_ws, documents, health, market


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configure logging and log startup/shutdown."""
    configure_logging()
    log.info("app.startup", env=settings.APP_ENV)
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

if settings.is_dev:
    from app.routers import debug

    app.include_router(debug.router)
