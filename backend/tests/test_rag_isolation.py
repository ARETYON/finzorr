"""Tenant isolation at the retrieval seam + small-doc full reading — the one place a refactor could
silently leak one user's documents to another.

These are SEAM tests: they prove the filter is constructed from exactly the
authenticated user's tenant (never anything client-controlled) and that
rag_node requests only [glossary, its user]. Qdrant-side enforcement is
proven by the live two-user smoke, not here (CI has no Qdrant service).
"""

from typing import Any

import pytest

pytestmark = pytest.mark.sanity


async def test_search_filter_carries_exactly_the_passed_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qdrant_client import models as qmodels

    from app.rag import vector_store

    captured: dict[str, Any] = {}

    class _FakeClient:
        async def query_points(self, *_a: Any, **kw: Any) -> Any:
            captured.update(kw)

            class _R:
                points: list[Any] = []

            return _R()

    async def no_ensure() -> None:
        return None

    monkeypatch.setattr(vector_store, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(vector_store, "ensure_collection", no_ensure)
    await vector_store.search([0.0] * 3, tenants=["glossary", "user-a"])

    conditions = captured["query_filter"].must
    assert len(conditions) == 1
    condition = conditions[0]
    assert isinstance(condition, qmodels.FieldCondition) and condition.key == "tenant"
    assert isinstance(condition.match, qmodels.MatchAny)
    assert condition.match.any == ["glossary", "user-a"]  # exactly, nothing more


async def test_rag_node_requests_only_glossary_and_own_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.graph.nodes import rag as rag_mod

    seen_tenants: list[list[str]] = []

    async def spy_search(_vector: Any, tenants: list[str], **_kw: Any) -> list[Any]:
        seen_tenants.append(list(tenants))
        return []

    async def fake_embed(_q: str) -> list[float]:
        return [0.0] * 3

    async def fake_stream(*_a: Any, **_k: Any) -> Any:
        from app.ai.base import StreamDone, Usage

        return StreamDone(text="no docs found", tool_calls=[], usage=Usage())

    monkeypatch.setattr(rag_mod, "search", spy_search)
    monkeypatch.setattr(rag_mod, "embed_query", fake_embed)
    monkeypatch.setattr(rag_mod, "stream", fake_stream)

    await rag_mod.rag_node({"user_msg": "what does my report say?", "user_id": "user-b"})
    assert seen_tenants == [["glossary", "user-b"]]

    # user B's request can never name user A — the tenant comes from the
    # authenticated state, and there is no code path that could add another
    seen_tenants.clear()
    await rag_mod.rag_node({"user_msg": "tell me about user-a's files", "user_id": "user-b"})
    assert seen_tenants == [["glossary", "user-b"]]


async def test_debug_pseudo_user_gets_no_private_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dev /debug route runs nodes with user_id='debug' — it must search
    the glossary ONLY, never a private tenant named 'debug'."""
    from app.graph.nodes import rag as rag_mod

    seen: list[list[str]] = []

    async def spy_search(_v: Any, tenants: list[str], **_kw: Any) -> list[Any]:
        seen.append(list(tenants))
        return []

    async def fake_embed(_q: str) -> list[float]:
        return [0.0] * 3

    async def fake_stream(*_a: Any, **_k: Any) -> Any:
        from app.ai.base import StreamDone, Usage

        return StreamDone(text="x", tool_calls=[], usage=Usage())

    monkeypatch.setattr(rag_mod, "search", spy_search)
    monkeypatch.setattr(rag_mod, "embed_query", fake_embed)
    monkeypatch.setattr(rag_mod, "stream", fake_stream)

    await rag_mod.rag_node({"user_msg": "define P/E", "user_id": "debug"})
    assert seen == [["glossary"]]


# ---------------------------------------------------- small-doc full reading


def _hit(score: float, tenant: str, doc_id: str = "", title: str = "doc.pdf") -> Any:
    from app.rag.vector_store import Hit

    return Hit(
        score=score, text="excerpt text", title=title, locator="p.1",
        tenant=tenant, doc_id=doc_id,
    )


async def test_glossary_only_hits_never_touch_db_or_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M8: zero-doc / glossary-only users take the exact legacy path —
    no Document query, no storage read."""
    from app.graph.nodes import rag as rag_mod

    touched = {"db": False}

    class _Boom:
        def __call__(self, *a: Any, **k: Any) -> Any:
            touched["db"] = True
            raise AssertionError("must not be constructed")

    monkeypatch.setattr("app.db.session.SessionLocal", _Boom())
    blocks, expanded = await rag_mod._expand_best_small_doc(
        "user-1", [_hit(0.9, "glossary")]
    )
    assert expanded is False
    assert touched["db"] is False
    assert blocks == [("doc.pdf", "excerpt text")]


async def test_small_doc_expands_to_full_labeled_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid as uuid_module

    from app.graph.nodes import rag as rag_mod

    user_id = str(uuid_module.uuid4())
    doc_id = str(uuid_module.uuid4())

    class _Row:
        chunk_count = 3
        storage_key = "k"
        filename = "report.pdf"

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return _Row()

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def execute(self, _q: Any) -> _Result:
            return _Result()

    class _Storage:
        async def load(self, _k: str) -> bytes:
            return b"raw"

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _Session())
    monkeypatch.setattr("app.documents.storage.get_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.documents.ingest.extract_any",
        lambda _f, _d: [("p.1", "page one full"), ("p.2", "page two full")],
    )

    hits = [
        _hit(0.9, user_id, doc_id, "report.pdf"),
        _hit(0.5, "glossary", "", "Margin"),
    ]
    blocks, expanded = await rag_mod._expand_best_small_doc(user_id, hits)
    assert expanded is True
    # full pages first (with locator labels), untouched glossary excerpt kept
    assert blocks[0] == ("report.pdf · p.1", "page one full")
    assert blocks[1] == ("report.pdf · p.2", "page two full")
    assert blocks[2] == ("Margin", "excerpt text")


async def test_large_doc_keeps_excerpt_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid as uuid_module

    from app.graph.nodes import rag as rag_mod

    user_id = str(uuid_module.uuid4())

    class _Row:
        chunk_count = 40  # > SMALL_DOC_CHUNKS
        storage_key = "k"
        filename = "big.pdf"

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return _Row()

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def execute(self, _q: Any) -> _Result:
            return _Result()

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _Session())
    hits = [_hit(0.9, user_id, str(uuid_module.uuid4()), "big.pdf")]
    blocks, expanded = await rag_mod._expand_best_small_doc(user_id, hits)
    assert expanded is False
    assert blocks == [("big.pdf · p.1", "excerpt text")]


async def test_expansion_failure_falls_back_to_excerpts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid as uuid_module

    from app.graph.nodes import rag as rag_mod

    user_id = str(uuid_module.uuid4())

    class _ExplodingSession:
        async def __aenter__(self) -> "_ExplodingSession":
            raise RuntimeError("db down")

        async def __aexit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _ExplodingSession())
    hits = [_hit(0.9, user_id, str(uuid_module.uuid4()))]
    blocks, expanded = await rag_mod._expand_best_small_doc(user_id, hits)
    assert expanded is False
    assert blocks == [("doc.pdf · p.1", "excerpt text")]


async def test_full_doc_char_cap_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid as uuid_module

    from app.graph.nodes import rag as rag_mod

    user_id = str(uuid_module.uuid4())

    class _Row:
        chunk_count = 2
        storage_key = "k"
        filename = "r.txt"

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return _Row()

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def execute(self, _q: Any) -> _Result:
            return _Result()

    class _Storage:
        async def load(self, _k: str) -> bytes:
            return b"raw"

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _Session())
    monkeypatch.setattr("app.documents.storage.get_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.documents.ingest.extract_any",
        lambda _f, _d: [("p.1", "x" * 20_000), ("p.2", "y" * 5_000)],
    )
    blocks, expanded = await rag_mod._expand_best_small_doc(
        user_id, [_hit(0.9, user_id, str(uuid_module.uuid4()), "r.txt")]
    )
    assert expanded is True
    total = sum(len(t) for _s, t in blocks)
    assert total <= rag_mod._FULL_DOC_CHARS
