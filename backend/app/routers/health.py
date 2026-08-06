"""Liveness and readiness probes.

`/healthz` must never touch a dependency (it gates deploys and uptime checks);
`/readyz` verifies Postgres and Redis reachability.
"""

from fastapi import APIRouter
from sqlalchemy import text

from app.schemas.misc import HealthOut, ReadyOut

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthOut)
async def healthz() -> HealthOut:
    """Liveness: process is up. No dependency checks by design."""
    return HealthOut(status="ok")


@router.get("/readyz", response_model=ReadyOut)
async def readyz() -> ReadyOut:
    """Readiness: verify DB and Redis connections; degrade per-dependency."""
    from app.db.session import engine
    from app.core.redis import get_redis

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        postgres = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness must report, not raise
        postgres = f"error: {type(exc).__name__}"
    try:
        await get_redis().ping()
        redis = "ok"
    except Exception as exc:  # noqa: BLE001
        redis = f"error: {type(exc).__name__}"
    ok = postgres == "ok" and redis == "ok"
    return ReadyOut(status="ok" if ok else "degraded", postgres=postgres, redis=redis)
