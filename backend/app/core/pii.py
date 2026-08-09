"""PII detection + trace-only redaction.

Two DISTINCT purposes, never conflated:
- `detect_pii` flags what TYPES of PII a document contains (metadata only,
  for audit visibility) — the document itself is never redacted or blocked;
  it's the user's own data and RAG needs the real content to answer
  questions about it. Access control (tenant isolation) is the actual
  cross-user boundary, not redaction of a user's own upload.
- `redact_for_trace` masks PII in text that is about to leave the box via
  LangSmith. It must NEVER be applied to what's sent to the LLM provider or
  what's stored — only to the copy that becomes a trace payload.
"""

import re

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_IN = re.compile(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pan", _PAN),  # before email/generic so PAN-shaped tokens aren't lost
    ("ifsc", _IFSC),
    ("email", _EMAIL),
    ("phone_in", _PHONE_IN),
    ("card_like", _CARD),
    ("aadhaar_like", _AADHAAR),
]


def detect_pii(text: str) -> list[str]:
    """Which PII TYPES appear in text — never the matched values."""
    return [name for name, pattern in _PATTERNS if pattern.search(text)]


def redact_for_trace(text: str) -> str:
    """Mask PII spans for a TRACE-ONLY copy. Never apply to stored content
    or to what's sent to the LLM — see module docstring."""
    redacted = text
    for name, pattern in _PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{name.upper()}]", redacted)
    return redacted
