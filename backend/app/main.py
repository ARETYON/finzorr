"""FastAPI application entrypoint.

Routers are registered here; heavy subsystems (graph, providers) are imported
lazily inside routers so a missing optional dependency never blocks startup.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, log, new_correlation_id
from app.routers import (
    attachments,
    auth,
    chat,
    chat_ws,
    documents,
    health,
    integrations,
    market,
    sharing,
    watchlist,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, register optional tool families, log lifecycle."""
    configure_logging()
    log.info("app.startup", env=settings.APP_ENV)
    from app.core.otel import setup_tracing

    setup_tracing()
    from app.core.langsmith_setup import setup_langsmith

    setup_langsmith()  # BEFORE any graph invocation — env vars + cache clear
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
    try:
        from app.tools_registry.code_tools import register_code_tools
        from app.tools_registry.image_tools import register_image_tools

        register_code_tools()
        register_image_tools()
    except Exception as exc:  # noqa: BLE001
        log.warning("startup.optional_tools_failed", error=str(exc))
    try:
        from app.integrations.google_connect import register_google_tools

        register_google_tools()
    except Exception as exc:  # noqa: BLE001
        log.warning("startup.google_connectors_failed", error=str(exc))
    scheduler_task = None
    if settings.SCHEDULER_ENABLED:
        import asyncio

        from app.scheduler import scheduler_loop

        scheduler_task = asyncio.create_task(scheduler_loop())
    yield
    if scheduler_task is not None:
        scheduler_task.cancel()
    try:
        from app.orchestration.graph import close_graph

        await close_graph()
    except Exception as exc:  # noqa: BLE001 — shutdown must not raise
        log.warning("shutdown.graph_close_failed", error=str(exc))
    log.info("app.shutdown")


app = FastAPI(title="finzorr.ai API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Bind a correlation id per request and surface it as X-Request-ID.

    An inbound X-Request-ID (proxy/LB propagation) is honored; the id is
    stashed on request.state so the 500 handler reports the SAME id the
    request's log lines carry — a mismatched id defeats log correlation.
    """
    inbound = request.headers.get("X-Request-ID", "")
    cid = new_correlation_id(inbound or None)
    request.state.request_id = cid
    response = await call_next(request)
    response.headers["X-Request-ID"] = cid
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Uniform 500 envelope — never leak internals, always give the request id
    so a user report can be matched to the structured logs."""
    cid = getattr(request.state, "request_id", "") or new_correlation_id()
    log.error("http.unhandled", path=request.url.path, error=str(exc))
    headers = {"X-Request-ID": cid}
    # This handler runs in ServerErrorMiddleware, OUTSIDE CORSMiddleware —
    # without these the browser reports a CORS failure instead of our envelope.
    if request.headers.get("origin") == settings.FRONTEND_ORIGIN:
        headers["Access-Control-Allow-Origin"] = settings.FRONTEND_ORIGIN
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Access-Control-Expose-Headers"] = "X-Request-ID"
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "code": "internal", "request_id": cid},
        headers=headers,
    )


install_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],  # otherwise the SPA can never read it
)

app.include_router(health.router)  # /healthz, /readyz — never versioned
app.include_router(chat_ws.router)  # /ws/chat — include ONCE (prefixes apply to WS routes)

# REST is mounted twice: /api/v1 is canonical, /api is the compatibility
# alias (same handlers, same behavior; operationIds are unique per path).
_API_ROUTERS = (
    auth.router,
    chat.router,
    market.router,
    documents.router,
    watchlist.router,
    attachments.router,
    integrations.router,
    sharing.router,
)
for _router in _API_ROUTERS:
    app.include_router(_router, prefix="/api/v1")
    app.include_router(_router, prefix="/api")

# The two hot lists diverge by version: v1 returns the cursor envelope
# {items, next_cursor, total}; the /api alias keeps the bare-list shape.
app.include_router(chat.v1_router, prefix="/api/v1")
app.include_router(chat.legacy_router, prefix="/api")

if settings.is_dev:
    from app.routers import debug

    app.include_router(debug.router, prefix="/api")
