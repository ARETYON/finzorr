"""Graph-native frame streaming via LangGraph's custom stream channel.

Nodes call `emit_frame(...)`; the frame surfaces through
`graph.astream(stream_mode="custom")` to whichever caller invoked THIS turn —
no global registry, so two tabs on one session can no longer steal each
other's tokens, and nothing blocks running multiple workers.

Outside a graph run (debug's standalone node tester, direct node calls in
tests) `get_stream_writer` has no runtime context — emit_frame degrades to a
no-op instead of raising.
"""

from typing import Any

from langgraph.config import get_stream_writer


def emit_frame(frame: dict[str, Any]) -> None:
    """Best-effort frame emission into the current graph run's stream."""
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 — no runtime context: not an error
        return
    writer(frame)
