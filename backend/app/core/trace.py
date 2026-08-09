"""Tiny LangSmith run-annotation helpers.

Inside a LangGraph node (or any @traceable body), `get_current_run_tree()`
returns THAT run — so metadata/tags can be attached without creating extra
runs. Both helpers are strict no-ops when tracing is disabled, and they
swallow every error: annotation must never be able to break a turn.
"""

from typing import Any

from langsmith import get_current_run_tree


def mark(**metadata: Any) -> None:
    """Attach metadata keys to the current run (scalars/short lists only)."""
    try:
        run_tree = get_current_run_tree()
        if run_tree is not None:
            run_tree.set(metadata=metadata)
    except Exception:  # noqa: BLE001, S110 — annotation is never load-bearing
        pass


def tag(*tags: str) -> None:
    """Attach tags to the current run (filterable facets in LangSmith)."""
    try:
        run_tree = get_current_run_tree()
        if run_tree is not None:
            run_tree.add_tags(list(tags))
    except Exception:  # noqa: BLE001, S110
        pass
