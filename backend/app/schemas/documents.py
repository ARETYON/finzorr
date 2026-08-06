"""Wire shapes for document upload and listing."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentUploadOut(BaseModel):
    id: uuid.UUID
    status: str
    chunks: int


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    chunks: int | None
    uploaded_at: datetime
