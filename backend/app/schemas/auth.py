"""Auth API schemas."""

import uuid

from pydantic import BaseModel


class GoogleLoginIn(BaseModel):
    id_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    picture_url: str | None
    custom_instructions: str | None = None

    model_config = {"from_attributes": True}


class UserUpdateIn(BaseModel):
    custom_instructions: str | None = None
