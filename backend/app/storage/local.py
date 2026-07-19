"""Local filesystem storage provider -- the only implemented backend today.

Dev/single-instance deployments only; app.storage.factory.StorageFactory is
the swap point for a real object-storage backend (S3/GCS) later.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from .base import BaseStorageProvider
from .config import config

# Strip anything that isn't alnum/dot/dash/underscore, so a client-supplied
# filename (e.g. "../../etc/passwd" or one containing path separators) can
# never escape the per-case upload directory.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename(filename: str) -> str:
    name = Path(filename).name  # drop any directory components
    name = _UNSAFE_CHARS.sub("_", name).strip("._") or "file"
    return name[:150]


class LocalFilesystemProvider(BaseStorageProvider):
    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or config.local_root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _abs_path(self, storage_path: str) -> Path:
        # storage_path is always relative (produced only by save() below),
        # so this can never resolve outside self._root.
        return self._root / storage_path

    def save(self, case_id: str, filename: str, content: bytes) -> str:
        safe_case_id = _UNSAFE_CHARS.sub("_", case_id)
        safe_name = _sanitize_filename(filename)
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
