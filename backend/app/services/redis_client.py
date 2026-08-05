"""Lazy singleton Redis client (redis-py asyncio)."""

from redis.asyncio import Redis

from app.core.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Return the shared async Redis client, creating it on first use."""
    global _client  # noqa: PLW0603 — deliberate module-level singleton
    if _client is None:
        _client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
