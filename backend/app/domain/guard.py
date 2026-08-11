"""Pure domain logic — no I/O, no framework dependencies. Safe to unit-test without mocks.

The deterministic pattern-floor tier of inbound/outbound message screening,
extracted from `app/core/guard.py` (which keeps the optional-LLM-tier
`screen_message` wrapper that does real I/O and calls `screen_floor` here).

OBSERVE-ONLY by design: a `suspicious` verdict tags the trace and logs; it
never blocks and never alters the model's inputs. Enforcement is a future
flag, deliberately not shipped until false-positive rates are measured.
"""

import re

# Anchored jailbreak markers. Each pattern must be specific enough that a
# benign finance message can't trip it ("ignore the noise, what's TCS at?"
# must pass) — the injection eval pins both directions.
_ATTACK_PATTERNS = re.compile(
    r"(ignore (all|your|previous|prior|the above) (previous |prior )?instructions"
    r"|disregard (all|your|previous|prior) (instructions|rules|guidelines)"
    r"|you are now (dan|in developer mode|unrestricted|jailbroken)"
    r"|pretend (you have no|there are no) (rules|restrictions|guidelines)"
    r"|do anything now"
    r"|reveal (your|the) (system prompt|hidden prompt|initial instructions)"
    r"|print (your|the) (system prompt|instructions above)"
    r"|repeat (everything|the text) (above|before this)"
    r"|act as (an? )?(unfiltered|unrestricted|uncensored)"
    r"|bypass (your|all|the) (safety|content|filter)"
    r"|new persona.{0,20}(no|without) (limits|restrictions))",
    re.IGNORECASE,
)


def screen_floor(text: str) -> str:
    """Deterministic tier: 'ok' | 'suspicious'. Never raises."""
    return "suspicious" if _ATTACK_PATTERNS.search(text) else "ok"


# Secret-shaped strings: if a document or recalled memory somehow leaked a
# key and the model echoed it back, catch it in the OUTPUT before it's
# shown to the user's own (legitimate) session — same pattern class used
# throughout this project's own commit/artifact scanning.
_SECRET_SHAPED = re.compile(
    r"\b(gsk_|AIza|sk-proj-|sk-[a-zA-Z0-9]{20}|eyJ[A-Za-z0-9_-]{10}"
    r"|lsv2_(pt|sk)_)[A-Za-z0-9_-]{10,}"
)

# A verbatim echo of the system prompt's own distinctive framing lines —
# the model should never be reciting its own instructions back to the user.
_SYSTEM_PROMPT_FRAGMENTS = (
    "You answer using ONLY the knowledge excerpts provided below",
    "You plan how an assistant answers, using these specialists",
)


def _is_degenerate(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lines = [line for line in stripped.splitlines() if line.strip()]
    if len(lines) > 5:
        most_common = max(lines, key=lines.count)
        if lines.count(most_common) > 5:
            return True
    return False


def screen_output(text: str) -> str:
    """Deterministic OUTPUT tier: 'ok' | 'suspicious'. Never raises, never
    alters the text — same observe-only contract as screen_floor."""
    if _SECRET_SHAPED.search(text):
        return "suspicious"
    if any(fragment in text for fragment in _SYSTEM_PROMPT_FRAGMENTS):
        return "suspicious"
    if _is_degenerate(text):
        return "suspicious"
    return "ok"
