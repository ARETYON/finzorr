"""Per-user sliding-window rate limit (Redis, best-effort).

Protects the free LLM quotas. Redis being down never blocks a user —
the limiter fails open by design.
"""

from app.core.config import settings
from app.core.logging import log


async def check_rate_limit(user_id: str) -> bool:
    """True if the user may send another message in the current window."""
    try:
        from app.services.redis_client import get_redis

        redis = get_redis()
        key = f"ratelimit:{user_id}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.RATE_LIMIT_WINDOW_S)
        return int(count) <= settings.RATE_LIMIT_MESSAGES
    except Exception:  # noqa: BLE001 — fail open
        log.warning("ratelimit.unavailable")
        return True
