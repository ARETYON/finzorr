"""DocumentStorage interface — local disk in dev, Cloudflare R2 at deploy.

The interface is the seam: swapping to R2 is one new class implementing
`DocumentStorage`, chosen by config, with zero caller changes.
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings


class DocumentStorage(ABC):
    """Binary blob storage keyed by `{user_id}/{doc_id}.pdf`-style keys."""

    @abstractmethod
    async def save(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    async def load(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class LocalDiskStorage(DocumentStorage):
    """Dev implementation under DOCUMENT_STORAGE_DIR (gitignored)."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root.resolve()):  # path-traversal jail
            raise ValueError(f"illegal storage key: {key}")
        return path

    async def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def load(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)


_storage: DocumentStorage | None = None


def get_storage() -> DocumentStorage:
    """Config-selected storage backend (local disk for now; R2 at deploy)."""
    global _storage  # noqa: PLW0603 — lazy singleton
    if _storage is None:
        _storage = LocalDiskStorage(settings.DOCUMENT_STORAGE_DIR)
    return _storage
