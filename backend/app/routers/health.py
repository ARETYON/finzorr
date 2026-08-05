"""Liveness and readiness probes.

`/healthz` must never touch a dependency (it gates deploys and uptime checks);
`/readyz` verifies Postgres and Redis reachability.
"""

from fastapi import APIRouter
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: process is up. No dependency checks by design."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    """Readiness: verify DB and Redis connections; degrade per-dependency."""
    from app.db.session import engine
    from app.services.redis_client import get_redis

    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness must report, not raise
        checks["postgres"] = f"error: {type(exc).__name__}"
    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    checks["status"] = "ok" if all(v == "ok" for k, v in checks.items() if k != "status") else "degraded"
    return checks
