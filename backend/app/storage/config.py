"""Central configuration for DigiNyaya's file storage layer.

Mirrors app.llm.config's shape (frozen dataclass, one env var per field).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageConfig:
    # local | s3 | gcs -- only "local" is implemented today (see
    # app.storage.factory.StorageFactory.create()).
    provider: str = os.getenv("DIGINYAYA_STORAGE_PROVIDER", "local").lower()

    # Root directory for the local filesystem provider. Relative paths are
    # resolved against the backend/ directory (three parents up from this
    # file: app/storage/config.py -> app/storage -> app -> backend).
    local_root: str = os.getenv(
        "DIGINYAYA_STORAGE_ROOT",
        str(Path(__file__).resolve().parents[2] / "uploads"),
    )

    max_upload_mb: int = int(os.getenv("DIGINYAYA_MAX_UPLOAD_MB", "15"))

    # Only read/used when provider == "s3" (see app.storage.s3.S3Provider).
    s3_bucket: str = os.getenv("DIGINYAYA_S3_BUCKET", "")


config = StorageConfig()
