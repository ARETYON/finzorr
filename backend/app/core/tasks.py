"""Tracked fire-and-forget tasks.

`asyncio.create_task` into a discarded local is GC-eligible mid-flight —
CPython keeps only a weak reference to running tasks. Every fire-and-forget
in the app goes through `spawn`, which holds a strong reference until the
task finishes and logs (instead of silently dropping) any crash.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.logging import log

_background: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
    """Run a coroutine in the background with a strong reference held."""
    task = asyncio.create_task(coro, name=name)
    _background.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.warning("background_task.failed", task=name, error=str(t.exception()))

    task.add_done_callback(_done)
    return task
