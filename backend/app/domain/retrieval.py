"""Pure domain logic — no I/O, no framework dependencies. Safe to unit-test without mocks.

Cosine similarity + Maximal Marginal Relevance re-ranking, extracted from
`app/rag/vector_store.py` (which does the actual Qdrant I/O and calls these
via import).
"""

from typing import Any


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _mmr_select(
    query_vector: list[float],
    candidates: list[tuple[Any, list[float]]],
    top_k: int,
    lam: float,
) -> list[Any]:
    """Maximal Marginal Relevance over already-fetched candidates (pure
    python cosine — no new dependency). ALWAYS keeps rank-1 by raw
    similarity first (top-1 floor: a diversity-heavy lambda can reshuffle
    positions 2+ but can never drop the single best hit), then greedily
    fills the rest balancing relevance-to-query against
    redundancy-to-already-selected."""
    if not candidates:
        return []
    scored = sorted(
        candidates, key=lambda c: _cosine(query_vector, c[1]), reverse=True
    )
    selected = [scored[0]]
    remaining = scored[1:]
    while remaining and len(selected) < top_k:
        best_idx, best_value = 0, float("-inf")
        for i, (_point, vec) in enumerate(remaining):
            relevance = _cosine(query_vector, vec)
            redundancy = max(_cosine(vec, s_vec) for _s_point, s_vec in selected)
            value = lam * relevance - (1 - lam) * redundancy
            if value > best_value:
                best_idx, best_value = i, value
        selected.append(remaining.pop(best_idx))
    return [point for point, _vec in selected]
