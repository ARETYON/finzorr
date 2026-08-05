"""Schema exposure for NL2SQL — whitelist first, always.

ALLOWED_TABLES is the single source of truth for what the model may query.
It deliberately excludes users/messages/watchlist_items/everything else.
"""

from app.models.fundamental import Fundamental

ALLOWED_TABLES: frozenset[str] = frozenset({"fundamentals"})


def schema_summary() -> str:
    """Compact DDL-ish description of the whitelisted tables for the prompt."""
    columns = ", ".join(
        f"{c.name} {c.type}" for c in Fundamental.__table__.columns
    )
    return f"fundamentals({columns})"
