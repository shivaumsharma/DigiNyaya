"""Local filesystem storage provider -- the only implemented backend today.

Dev/single-instance deployments only; app.storage.factory.StorageFactory is
the swap point for a real object-storage backend (S3/GCS) later.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .base import BaseStorageProvider
from .config import config
from .sanitize import sanitize_case_id, sanitize_filename


class LocalFilesystemProvider(BaseStorageProvider):
    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or config.local_root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _abs_path(self, storage_path: str) -> Path:
        # storage_path is always relative (produced only by save() below),
        # so this can never resolve outside self._root.
        return self._root / storage_path

    def save(self, case_id: str, filename: str, content: bytes) -> str:
        safe_case_id = sanitize_case_id(case_id)
        safe_name = sanitize_filename(filename)
        rel_path = Path(safe_case_id) / f"{uuid.uuid4().hex}_{safe_name}"
        abs_path = self._abs_path(str(rel_path))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        return str(rel_path).replace("\\", "/")

    def read(self, storage_path: str) -> bytes:
        return self._abs_path(storage_path).read_bytes()

    def delete(self, storage_path: str) -> None:
        path = self._abs_path(storage_path)
        if path.exists():
            path.unlink()

    def exists(self, storage_path: str) -> bool:
        return self._abs_path(storage_path).exists()
