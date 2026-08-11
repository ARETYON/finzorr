"""Runtime citation-marker validation — the exact logic extracted from
evals/grounded_eval.py so it runs live, not only in a manual eval."""

import pytest

from app.domain.citations import find_invalid_markers

pytestmark = pytest.mark.sanity


def test_in_range_markers_pass() -> None:
    assert find_invalid_markers("TCS grew [1] and Infy fell [2].", 2) == []


def test_out_of_range_marker_flagged() -> None:
    assert find_invalid_markers("Revenue grew [3].", 2) == [3]


def test_multiple_invalid_markers_all_flagged() -> None:
    assert find_invalid_markers("[5] and [7] were cited", 2) == [5, 7]


def test_no_markers_is_a_noop() -> None:
    assert find_invalid_markers("Plain text, no citations at all.", 3) == []


def test_zero_citations_flags_any_marker() -> None:
    assert find_invalid_markers("as shown in [1]", 0) == [1]


def test_markers_split_across_renumbered_text() -> None:
    # compose's renumbering can leave markers anywhere in the text; the
    # check is a pure text scan, order-independent
    text = "First [2], then [1], then [2] again."
    assert find_invalid_markers(text, 2) == []
    assert find_invalid_markers(text, 1) == [2]
