"""Wire shapes for share links and personas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ShareCreateOut(BaseModel):
    token: str
    expires_at: datetime | None


class SharedMessageOut(BaseModel):
    role: str
    content: str
    route: str | None


class SharedChatOut(BaseModel):
    title: str
    messages: list[SharedMessageOut]


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    system_prompt: str = Field(min_length=1, max_length=4000)


class PersonaOut(BaseModel):
    id: uuid.UUID
    name: str
    system_prompt: str

    model_config = {"from_attributes": True}


class PersonaCreateOut(BaseModel):
    id: uuid.UUID


class SessionPersonaIn(BaseModel):
    persona_id: uuid.UUID | None


class SessionPersonaOut(BaseModel):
    persona_id: uuid.UUID | None
