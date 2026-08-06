"""Wire shapes for the smaller endpoint families.

Kept in one module because each family has only 1-3 shapes; split out a
domain module the moment one grows.
"""

from datetime import datetime

from pydantic import BaseModel

# --- watchlist ---

class WatchlistItemOut(BaseModel):
    symbol: str
    exchange: str
    added_at: datetime


class WatchlistAddOut(BaseModel):
    symbol: str
    status: str


# --- market ---

class QuoteOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    price: float
    currency: str
    day_change_pct: float | None
    volume: int | None
    as_of: str


# --- chat search / feedback ---

class SearchHitOut(BaseModel):
    session_id: str
    session_title: str
    role: str
    snippet: str
    created_at: str


class FeedbackCreateOut(BaseModel):
    id: str


# --- auth extras ---

class MemoryFactOut(BaseModel):
    id: str
    text: str


class LogoutOut(BaseModel):
    ok: bool


# --- attachments ---

class AttachmentUploadOut(BaseModel):
    token: str
    mime: str


# --- health ---

class HealthOut(BaseModel):
    status: str


class ReadyOut(BaseModel):
    status: str
    postgres: str
    redis: str


# --- HITL approvals ---

class PendingApprovalOut(BaseModel):
    pending: bool
    tools: list[dict[str, object]]
