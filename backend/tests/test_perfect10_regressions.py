"""In-wave regressions for the Perfect-10 wave's fine-grained mechanics:
citation renumbering, the research cache key, and the BaseStore memory path.
(The envelope, replan, parallel, and HITL surfaces have their own files.)
"""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.graph.graph import _research_search_key
from app.graph.nodes.compose import renumber_steps

# ---------------------------------------------------------------- renumbering


class TestRenumberSteps:
    def test_colliding_markers_get_global_sequence(self) -> None:
        outputs = [
            {"output": "TCS rose [1].", "citations": [{"marker": "[1]", "snippet": "url-a"}]},
            {"output": "Infy fell [1].", "citations": [{"marker": "[1]", "snippet": "url-b"}]},
        ]
        texts, merged = renumber_steps(outputs)
        assert texts == ["TCS rose [1].", "Infy fell [2]."]
        assert [c["marker"] for c in merged] == ["[1]", "[2]"]
        assert [c["snippet"] for c in merged] == ["url-a", "url-b"]

    def test_swap_chain_does_not_cascade(self) -> None:
        # [1]->[2] must not be re-hit by the later [2]->[3] rewrite
        outputs = [
            {"output": "x", "citations": [{"marker": "[9]", "snippet": "u0"}]},
            {
                "output": "see [1] and [2]",
                "citations": [
                    {"marker": "[1]", "snippet": "u1"},
                    {"marker": "[2]", "snippet": "u2"},
                ],
            },
        ]
        texts, merged = renumber_steps(outputs)
        assert texts[1] == "see [2] and [3]"
        assert [c["marker"] for c in merged] == ["[1]", "[2]", "[3]"]

    def test_bracketed_numbers_without_citation_are_untouched(self) -> None:
        # a bare [\d+] sweep would rewrite code/prose — only cited markers move
        outputs: list[dict[str, Any]] = [
            {"output": "arr[1] = 5  # [3] is prose", "citations": []},
            {"output": "cited [1]", "citations": [{"marker": "[1]", "snippet": "u"}]},
        ]
        texts, _merged = renumber_steps(outputs)
        assert texts[0] == "arr[1] = 5  # [3] is prose"
        assert texts[1] == "cited [1]"

    def test_non_dict_citations_are_skipped(self) -> None:
        texts, merged = renumber_steps([{"output": "t", "citations": ["junk", None]}])
        assert texts == ["t"]
        assert merged == []


# ---------------------------------------------------------------- cache key


class TestResearchSearchKey:
    def test_key_ignores_everything_but_subs(self) -> None:
        a = _research_search_key(
            {"research_subs": ["q1", "q2"], "turn_id": "t1", "messages": ["x"]}
        )
        b = _research_search_key(
            {"research_subs": ["q1", "q2"], "turn_id": "t2", "messages": ["y"]}
        )
        assert a == b  # the default whole-state key would never hit twice

    def test_key_is_order_insensitive(self) -> None:
        assert _research_search_key({"research_subs": ["b", "a"]}) == _research_search_key(
            {"research_subs": ["a", "b"]}
        )

    def test_different_subs_differ(self) -> None:
        assert _research_search_key({"research_subs": ["a"]}) != _research_search_key(
            {"research_subs": ["b"]}
        )


# ---------------------------------------------------------------- store memory


@dataclass
class _Item:
    key: str
    value: dict[str, Any]
    score: float | None = None


class _FakeStore:
    """Records BaseStore calls; asearch serves whatever was put."""

    def __init__(self) -> None:
        self.puts: list[tuple[tuple[str, str], str, dict[str, Any]]] = []
        self.deletes: list[tuple[tuple[str, str], str]] = []
        self.search_scores: dict[str, float | None] = {}

    async def aput(self, namespace: tuple[str, str], key: str, value: dict[str, Any]) -> None:
        self.puts.append((namespace, key, value))

    async def asearch(self, namespace: tuple[str, str], **_kw: Any) -> list[_Item]:
        return [
            _Item(key=k, value=v, score=self.search_scores.get(str(v.get("text"))))
            for ns, k, v in self.puts
            if ns == namespace
        ]

    async def adelete(self, namespace: tuple[str, str], key: str) -> None:
        self.deletes.append((namespace, key))


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    from app.graph import graph as graph_mod

    store = _FakeStore()
    monkeypatch.setattr(graph_mod, "get_store", lambda: store)
    return store


async def test_extract_and_store_uses_store_namespace(
    fake_store: _FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.memory import facts

    async def fake_complete(*_a: Any, **_k: Any) -> str:
        return '["prefers bullet points", "beginner investor"]'

    monkeypatch.setattr(facts, "complete", fake_complete)
    stored = await facts.extract_and_store("user-1", "format?", "sure")
    assert stored == 2
    assert [ns for ns, _k, _v in fake_store.puts] == [("memories", "user-1")] * 2
    assert {v["text"] for _ns, _k, v in fake_store.puts} == {
        "prefers bullet points",
        "beginner investor",
    }
    # keys are deterministic (uuid5 of user/fact) so re-extraction dedupes
    key = fake_store.puts[0][1]
    assert key == str(
        uuid.uuid5(uuid.NAMESPACE_URL, "user-1/fact/prefers bullet points")
    )


async def test_recall_filters_by_score_and_scopes_namespace(fake_store: _FakeStore) -> None:
    from app.memory import facts

    await fake_store.aput(("memories", "user-1"), "k1", {"text": "prefers Hindi"})
    await fake_store.aput(("memories", "user-1"), "k2", {"text": "weak match"})
    await fake_store.aput(("memories", "user-2"), "k3", {"text": "other user's fact"})
    fake_store.search_scores = {"prefers Hindi": 0.9, "weak match": 0.2}

    recalled = await facts.recall("user-1", "language?")
    assert recalled == ["prefers Hindi"]  # 0.2 < RECALL_MIN_SCORE; user-2 unseen


async def test_list_and_delete_stay_in_namespace(fake_store: _FakeStore) -> None:
    from app.memory import facts

    await fake_store.aput(("memories", "user-1"), "k1", {"text": "fact"})
    listed = await facts.list_facts("user-1")
    assert listed == [{"id": "k1", "text": "fact"}]

    await facts.delete_fact("user-1", "k1")
    assert fake_store.deletes == [(("memories", "user-1"), "k1")]
