"""Redis TTL cache for market-data calls (best-effort, fail-open).

Applied at the call site, not inside providers, so providers stay swappable
without dragging cache logic along.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from app.core.logging import log

QUOTE_TTL_S = 45
OVERVIEW_TTL_S = 3600
HISTORY_TTL_S = 21600
SEARCH_TTL_S = 86400


def _serialize(value: Any) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        return json.dumps(asdict(value))
    if isinstance(value, list):
        return json.dumps(
            [asdict(v) if is_dataclass(v) and not isinstance(v, type) else v for v in value]
        )
    return json.dumps(value)


async def cached_json(
    key: str, ttl_s: int, fetch: Callable[[], Awaitable[Any]]
) -> Any:
    """Return the cached JSON value for `key`, or fetch+store it.

    Redis being unavailable degrades to a direct fetch — never an error.
    """
    redis = None
    try:
        from app.infrastructure.redis import get_redis

        redis = get_redis()
        hit = await redis.get(key)
        if hit is not None:
            return json.loads(hit)
    except Exception:  # noqa: BLE001
        log.warning("market_cache.read_failed", key=key)
    value = await fetch()
    if redis is not None:
        try:
            await redis.set(key, _serialize(value), ex=ttl_s)
        except Exception:  # noqa: BLE001
            log.warning("market_cache.write_failed", key=key)
    return value
