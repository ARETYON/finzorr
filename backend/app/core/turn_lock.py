"""Per-session turn lock — one in-flight graph invocation per thread_id.

Redis `SET NX EX` makes the guard hold across WORKERS (the process-local set
only ever protected one process — the last blocker to running multiple
uvicorn workers). The TTL self-heals an orphaned claim (process crash between
claim and release). Redis down → degrade to the local set: single-process
safety is preserved, multi-worker safety honestly isn't (logged).
"""

from app.core.config import settings
from app.core.logging import log

_local: set[str] = set()


def _ttl_s() -> int:
    return settings.TURN_TIMEOUT_S + 30


async def claim(session_id: str) -> bool:
    """Try to claim the session for one turn; False if already claimed."""
    if session_id in _local:
        return False
    try:
        from app.core.redis import get_redis

        acquired = await get_redis().set(
            f"turn_lock:{session_id}", "1", nx=True, ex=_ttl_s()
        )
        if not acquired:
            return False
    except Exception as exc:  # noqa: BLE001 — degrade to process-local safety
        log.warning("turn_lock.redis_unavailable", error=str(exc))
    _local.add(session_id)
    return True


async def release(session_id: str) -> None:
    """Release the claim (idempotent; best-effort on the Redis side)."""
    _local.discard(session_id)
    try:
        from app.core.redis import get_redis

        await get_redis().delete(f"turn_lock:{session_id}")
    except Exception:  # noqa: BLE001 — the TTL is the backstop
        log.warning("turn_lock.release_failed", session_id=session_id)
