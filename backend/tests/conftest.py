"""Shared fixtures: a real Postgres test database + authenticated API clients.

Env is pinned BEFORE any `app.*` import so the settings singleton binds to the
test database (env vars beat .env in pydantic-settings). Schema comes from
Base.metadata (not alembic) — migration correctness is checked separately.
Marked `integration`: needs a reachable Postgres (dev docker or CI service).
"""

import os

os.environ["APP_ENV"] = "dev"
os.environ["DEV_FAKE_AUTH"] = "true"
os.environ["COOKIE_DOMAIN"] = ""
# Tests must NEVER trace to LangSmith: the developer's local .env may have
# tracing on, and test_lifespan boots the real lifespan — without this pin
# the whole suite live-posts trace batches from background threads.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_API_KEY"] = ""
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://finzorr:finzorr@localhost:5433/finzorr_test",
)

# Hard guard: this suite DROPS and TRUNCATES tables. A mistyped
# TEST_DATABASE_URL pointing at a dev/prod DB must fail fast, not wipe it.
if not os.environ["DATABASE_URL"].rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError(
        "refusing to run destructive tests: database name must end with '_test' "
        f"(got {os.environ['DATABASE_URL'].rsplit('/', 1)[-1]!r})"
    )

import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import app.models  # noqa: F401, E402 — registers every table on Base.metadata
from app.auth.jwt_session import SESSION_COOKIE, create_session_jwt  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402

_schema_ready = False


async def _ensure_database() -> None:
    """Create the test database if the server doesn't have it yet."""
    url = os.environ["DATABASE_URL"]
    dbname = url.rsplit("/", 1)[-1]
    admin = create_async_engine(
        url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        await admin.dispose()


@pytest.fixture
async def _database() -> AsyncIterator[None]:
    """Fresh schema once per run; empty tables + a fresh pool per test."""
    global _schema_ready  # noqa: PLW0603
    # Discard anything a PRIOR test left in the pool: a connection created on
    # another (now-closed) event loop poisons every later checkout with
    # "attached to a different loop" — dispose defensively at setup, not
    # only at teardown.
    await engine.dispose()
    if not _schema_ready:
        await _ensure_database()
        async with engine.begin() as conn:
            # messages has a gin_trgm_ops index — extension must exist first
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        _schema_ready = True
    yield
    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    # Pooled asyncpg connections are bound to this test's (closing) event
    # loop — dispose here so the next test starts with a clean pool.
    await engine.dispose()


@pytest.fixture
async def client(_database: None) -> AsyncIterator[AsyncClient]:
    """Unauthenticated API client against the real app (no lifespan)."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def user_client(client: AsyncClient) -> AsyncClient:
    """Client authenticated as the dev user via the real login endpoint."""
    response = await client.post("/api/auth/dev-login")
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def other_client(_database: None) -> AsyncIterator[AsyncClient]:
    """A SECOND authenticated user — the attacker in ownership tests."""
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        user = User(
            google_sub=f"other-{uuid.uuid4().hex[:8]}",
            email="other@localhost",
            name="Other User",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.cookies.set(SESSION_COOKIE, create_session_jwt(user.id))
        yield c
