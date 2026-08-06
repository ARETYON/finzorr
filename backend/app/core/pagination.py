"""Shared limit/offset pagination for every list endpoint.

One dependency so the caps are uniform: default 50, hard cap 200. Endpoints
that return time-ordered history use "newest window" semantics — they take
the most recent `limit` rows and return them in display (ascending) order.
"""

from dataclasses import dataclass

from fastapi import Query


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int


def page_params(
    limit: int = Query(50, ge=1, le=200, description="max rows to return"),
    offset: int = Query(0, ge=0, description="rows to skip"),
) -> Page:
    return Page(limit=limit, offset=offset)
