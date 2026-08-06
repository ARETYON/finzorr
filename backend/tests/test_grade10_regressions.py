"""Regression tests for the grade-10 wave's OWN properties.

The final review's sharpest finding: the previous wave shipped tests for the
wave before it, but nothing defended what it had just introduced. These lock
in: the capped messages reducer, instruction splicing, supervisor routing
context, WS frame robustness (the session-brick regression), attachment
parsing, fence coverage of search snippets, and the correlation-id inherit.
All deterministic.
"""

import pytest

pytestmark = pytest.mark.sanity


# ------------------------------------------------- capped messages reducer

class TestCappedMessages:
    def test_caps_at_newest_60(self) -> None:
        from app.graph.state import MESSAGES_CAP, capped_messages

        existing = [{"role": "user", "content": str(i)} for i in range(100)]
        merged = capped_messages(existing, [{"role": "assistant", "content": "new"}])
        assert len(merged) == MESSAGES_CAP
        assert merged[-1]["content"] == "new"  # newest kept
        assert merged[0]["content"] == str(100 - MESSAGES_CAP + 1)  # oldest dropped

    def test_small_lists_pass_through(self) -> None:
        from app.graph.state import capped_messages

        assert capped_messages([{"role": "user", "content": "a"}], []) == [
            {"role": "user", "content": "a"}
        ]


# ------------------------------------------------- instruction splicing

class TestWithInstructions:
    def test_appends_when_present(self) -> None:
        from app.graph.nodes.common import with_instructions

        out = with_instructions("BASE", {"user_instructions": "reply briefly"})
        assert out.startswith("BASE")
        assert "reply briefly" in out

    def test_noop_when_absent(self) -> None:
        from app.graph.nodes.common import with_instructions

        assert with_instructions("BASE", {}) == "BASE"


# ------------------------------------------------- supervisor routing context

class TestRoutingContext:
    def test_includes_previous_exchange_truncated(self) -> None:
        from app.graph.state import AssistantState
        from app.graph.supervisor import _CONTEXT_CHARS, _routing_context

        state: AssistantState = {
            "messages": [
                {"role": "user", "content": "tell me about TCS"},
                {"role": "assistant", "content": "x" * 500},
            ]
        }
        context = _routing_context(state)
        assert "tell me about TCS" in context
        assert "x" * (_CONTEXT_CHARS + 1) not in context  # truncated

    def test_empty_without_history(self) -> None:
        from app.graph.supervisor import _routing_context

        assert _routing_context({}) == ""
        assert _routing_context({"messages": []}) == ""


# ------------------------------------------------- WS frame robustness

class TestWsFrameRobustness:
    """The session-brick regression: malformed client data must never raise
    AFTER the per-session guard claims the id (nothing would release it)."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"attachments": None},
            {"attachments": "not-a-list"},
            {"attachments": 42},
            {"attachments": [1, {"x": 1}, None]},
            {},
        ],
    )
    def test_parse_attachments_tolerates_any_shape(self, payload: dict[str, object]) -> None:
        from app.routers.chat_ws import _parse_attachments

        assert _parse_attachments(payload) == []

    def test_parse_attachments_keeps_first_string(self) -> None:
        from app.routers.chat_ws import _parse_attachments

        assert _parse_attachments({"attachments": ["a.png", "b.png"]}) == ["a.png"]

    def test_attachment_parsing_happens_before_guard_claim(self) -> None:
        """Structural check: in start_turn's source, _parse_attachments must
        precede the turn-lock claim — parsing after the claim re-opens the
        permanent-lockout bug."""
        import inspect

        from app.routers.chat_ws import _Connection

        source = inspect.getsource(_Connection.start_turn)
        assert source.index("_parse_attachments") < source.index("claim_turn")
        assert "except BaseException" in source  # failed start releases the claim


# ------------------------------------------------- fence coverage

class TestSnippetFencing:
    def test_search_snippets_are_wrapped(self) -> None:
        """Titles/snippets are attacker-influenceable; both consumers must
        fence them, not only the fetched page bodies."""
        import inspect

        from app.graph.nodes import research, web_search

        assert "wrap_untrusted" in inspect.getsource(web_search.web_search_node)
        assert "wrap_untrusted" in inspect.getsource(research.research_synthesize_node)


# ------------------------------------------------- correlation id inherit

class TestCorrelationId:
    def test_inherits_sanitized_upstream_id(self) -> None:
        from app.core.logging import new_correlation_id

        assert new_correlation_id("abc-123") == "abc-123"
        # header junk is stripped, length capped
        cid = new_correlation_id("x" * 200 + "\r\nInjected: yes")
        assert len(cid) <= 64
        assert "\r" not in cid and " " not in cid

    def test_mints_fresh_when_absent(self) -> None:
        from app.core.logging import new_correlation_id

        assert len(new_correlation_id()) == 12
        assert len(new_correlation_id("")) == 12
