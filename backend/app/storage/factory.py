"""Storage Provider Factory -- mirrors app.llm.factory's shape exactly.

Selection order: explicit provider from config only (no auto-detect chain
like the LLM factory has, since there's only one real implementation today).
"""

from __future__ import annotations

from .base import BaseStorageProvider
from .config import config
from .local import LocalFilesystemProvider


class StorageFactory:
    @staticmethod
    def create() -> BaseStorageProvider:
        provider = config.provider

        if provider == "local":
            return LocalFilesystemProvider()

        if provider in ("s3", "gcs"):
            # Not implemented yet -- add a provider class in this package
            # (mirroring local.py's BaseStorageProvider implementation) and
            # a branch here when a real backend is needed. Deliberately no
            # stub s3.py/gcs.py file: an empty stub with no real code isn't
            # a meaningful extension point, this branch already is one.
            raise NotImplementedError(
                f"Storage provider '{provider}' is not yet implemented. "
                "See app.storage.factory.StorageFactory.create()."
            )

        raise ValueError(f"Unknown storage provider '{provider}'")


_storage: BaseStorageProvider | None = None


def get_storage() -> BaseStorageProvider:
    """Return a singleton storage provider instance."""
    global _storage
    if _storage is None:
        _storage = StorageFactory.create()
    return _storage


def reload_storage() -> BaseStorageProvider:
    """Recreate the storage provider instance."""
    global _storage
    _storage = StorageFactory.create()
    return _storage
