"""Groundedness / citation-validity eval (LIVE — needs a running provider).

For a small set of web_search and research prompts, runs the REAL node and
checks structural grounding properties no LLM judge is needed for:
- every [n] citation marker in the answer resolves to a provided source,
- research reports contain a Sources section,
- answers are non-empty when sources were found.

This is deliberately a live eval (network + LLM): run it locally or in the
launch-gate environment, not in PR CI.

Usage: uv run python -m evals.grounded_eval
"""

import asyncio
import re
import sys

_MARKER = re.compile(r"\[(\d+)\]")

WEB_PROMPTS = [
    "What's the latest news about the Indian stock market?",
    "Recent developments in UPI payments",
]
RESEARCH_PROMPTS = [
    "Do deep research on index funds in India",
]
# RAG's own citations were never checked here — the X11-class gap this
# closes. A throwaway tenant + tiny fixture doc, same pattern as rag_eval.py.
RAG_PROMPTS = ["What was the total revenue mentioned in the document?"]


async def _eval_web(prompt: str) -> list[str]:
    from app.specialists.web_search import web_search_node

    failures: list[str] = []
    out = await web_search_node({"session_id": "eval", "user_msg": prompt})
    text = out.get("final_text", "")
    citations = out.get("citations", [])
    if not text:
        return [f"web[{prompt[:30]}]: empty answer"]
    markers = {int(m) for m in _MARKER.findall(text)}
    valid = set(range(1, len(citations) + 1))
    for marker in markers - valid:
        failures.append(f"web[{prompt[:30]}]: dangling citation [{marker}]")
    return failures


async def _eval_research(prompt: str) -> list[str]:
    from typing import Any

    from app.specialists.research import (
        research_plan_node,
        research_read_node,
        research_search_node,
        research_synthesize_node,
    )

    failures: list[str] = []
    state: dict[str, Any] = {"session_id": "eval", "user_msg": prompt}
    for node in (
        research_plan_node,
        research_search_node,
        research_read_node,
        research_synthesize_node,
    ):
        state = {**state, **(await node(state))}  # type: ignore[arg-type]
    text = str(state.get("final_text", ""))
    citations = state.get("citations", [])
    if not text:
        return [f"research[{prompt[:30]}]: empty report"]
    if "## Sources" not in text and "Sources" not in text:
        failures.append(f"research[{prompt[:30]}]: no Sources section")
    markers = {int(m) for m in _MARKER.findall(text.split("Sources")[0])}
    valid = set(range(1, len(citations) + 1))
    for marker in markers - valid:
        failures.append(f"research[{prompt[:30]}]: dangling citation [{marker}]")
    return failures


async def _eval_rag(prompt: str) -> list[str]:
    import uuid

    from app.documents.ingest import ingest_document
    from app.rag.vector_store import delete_tenant
    from app.specialists.rag import rag_node

    tenant_uuid = uuid.uuid4()
    doc_text = (
        "Quarterly report. Total revenue was 812 crore rupees. "
        "Operating costs rose 6 percent year over year."
    )
    failures: list[str] = []
    try:
        await ingest_document(
            tenant_uuid, uuid.uuid4(), "grounded-fixture.txt", doc_text.encode()
        )
        out = await rag_node(
            {"session_id": "eval", "user_msg": prompt, "user_id": str(tenant_uuid)}
        )
        text = str(out.get("final_text", ""))
        citations = out.get("citations", [])
        if not text:
            return [f"rag[{prompt[:30]}]: empty answer"]
        markers = {int(m) for m in _MARKER.findall(text)}
        valid = set(range(1, len(citations) + 1))
        for marker in markers - valid:
            failures.append(f"rag[{prompt[:30]}]: dangling citation [{marker}]")
    finally:
        import contextlib

        with contextlib.suppress(Exception):  # cleanup is best-effort
            await delete_tenant(str(tenant_uuid))
    return failures


async def main() -> int:
    failures: list[str] = []
    for prompt in WEB_PROMPTS:
        failures += await _eval_web(prompt)
        print(".", end="", flush=True)
    for prompt in RESEARCH_PROMPTS:
        failures += await _eval_research(prompt)
        print(".", end="", flush=True)
    for prompt in RAG_PROMPTS:
        failures += await _eval_rag(prompt)
        print(".", end="", flush=True)
    print()
    total = len(WEB_PROMPTS) + len(RESEARCH_PROMPTS) + len(RAG_PROMPTS)
    print(f"groundedness: {total - len(failures)} clean / {total} prompts")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
