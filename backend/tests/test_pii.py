"""PII detection (types only, never values) + trace-only redaction."""

import pytest

from app.domain.pii import detect_pii, redact_for_trace

pytestmark = pytest.mark.sanity


class TestDetectPii:
    def test_detects_each_type(self) -> None:
        assert detect_pii("contact me at user@example.com") == ["email"]
        assert detect_pii("call 9876543210 for details") == ["phone_in"]
        assert detect_pii("PAN is ABCDE1234F") == ["pan"]
        assert detect_pii("IFSC code HDFC0001234") == ["ifsc"]
        assert "aadhaar_like" in detect_pii("id 1234 5678 9012")

    def test_returns_types_not_values(self) -> None:
        types = detect_pii("email user@example.com")
        assert types == ["email"]
        assert "user@example.com" not in types

    def test_clean_text_detects_nothing(self) -> None:
        assert detect_pii("What was the total revenue this quarter?") == []

    def test_multiple_types_in_one_document(self) -> None:
        text = "Contact user@example.com or call 9876543210. PAN: ABCDE1234F"
        types = detect_pii(text)
        assert set(types) == {"email", "phone_in", "pan"}


class TestRedactForTrace:
    def test_masks_email(self) -> None:
        redacted = redact_for_trace("reach me at user@example.com please")
        assert "user@example.com" not in redacted
        assert "[REDACTED:EMAIL]" in redacted

    def test_masks_pan(self) -> None:
        redacted = redact_for_trace("PAN ABCDE1234F on file")
        assert "ABCDE1234F" not in redacted
        assert "[REDACTED:PAN]" in redacted

    def test_clean_text_unchanged(self) -> None:
        text = "Reliance Industries reported record profits this quarter."
        assert redact_for_trace(text) == text

    def test_does_not_over_redact_finance_numbers(self) -> None:
        # a stock price / percentage must not accidentally match card/aadhaar
        # patterns (both need contiguous 12-16 digit runs)
        text = "TCS is trading at 3542.50, up 2.3% today"
        assert redact_for_trace(text) == text
