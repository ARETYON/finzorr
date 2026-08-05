"""Chat REST schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    route: str | None
    tool_calls: list[dict[str, Any]] | None
    citations: list[dict[str, Any]] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=2000)
