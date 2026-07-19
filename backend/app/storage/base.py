"""Abstract interface for all file storage providers.

Every provider (local filesystem, S3, GCS, ...) must implement this contract
so the rest of DigiNyaya remains storage-agnostic -- mirrors app.llm.base's
BaseLLMProvider shape for the same "swap the backend later" reason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseStorageProvider(ABC):
    """Base interface for every file storage backend."""

    @abstractmethod
    def save(self, case_id: str, filename: str, content: bytes) -> str:
        """Persist ``content`` and return an opaque ``storage_path`` that a
        later ``read()``/``delete()`` call can use to find it again. The
        returned path is a storage-relative identifier, never an absolute
        filesystem path -- callers must not assume anything about its shape
        beyond "pass it back to this same provider later"."""
        raise NotImplementedError

    @abstractmethod
    def read(self, storage_path: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def delete(self, storage_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, storage_path: str) -> bool:
        raise NotImplementedError
