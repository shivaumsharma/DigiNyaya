"""Translation cache for the Language Gateway.

Caches Sarvam translation results so repeated or identical text does not
trigger redundant API calls. Uses a two-tier design:

  1. In-memory LRU for hot entries (fast path on every request).
  2. Disk-backed JSON store under ``backend/data_cache/translations/`` so
     cache survives process restarts (same pattern as judgment ingestion).

Cache keys are derived from (source_lang, target_lang, normalized text) so
lookups are deterministic regardless of incidental whitespace differences.

Settings are loaded from ``app.language.config``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.language.config import CacheSettings, config as language_config

logger = logging.getLogger("diginyaya.language.cache")


@dataclass
class CacheEntry:
    """A single cached translation."""

    source_lang: str
    target_lang: str
    translated_text: str
    text_hash: str
    created_at: float
    provider: str = "sarvam"
    request_id: str | None = None

    def is_expired(self, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return False
        return (time.time() - self.created_at) > ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheEntry:
        return cls(
            source_lang=str(data["source_lang"]),
            target_lang=str(data["target_lang"]),
            translated_text=str(data["translated_text"]),
            text_hash=str(data["text_hash"]),
            created_at=float(data["created_at"]),
            provider=str(data.get("provider", "sarvam")),
            request_id=data.get("request_id"),
        )


@dataclass
class CacheStats:
    """Counters exposed for observability and health checks."""

    hits: int = 0
    misses: int = 0
    memory_hits: int = 0
    disk_hits: int = 0
    sets: int = 0
    evictions: int = 0
    expired: int = 0
    disabled_skips: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def normalize_text(text: str) -> str:
    """Normalize text before hashing so equivalent strings share a cache key."""
    normalized = unicodedata.normalize("NFC", text.strip())
    return " ".join(normalized.split())


def make_cache_key(source_lang: str, target_lang: str, text: str) -> str:
    """Return a stable SHA-256 hex digest for a translation lookup."""
    payload = f"{source_lang.strip().lower()}|{target_lang.strip().lower()}|{normalize_text(text)}"
    return sha256(payload.encode("utf-8")).hexdigest()


class TranslationCache:
    """Thread-safe two-tier cache for translation results."""

    def __init__(self, settings: CacheSettings | None = None) -> None:
        self.settings = settings or language_config.cache
        self._lock = threading.RLock()
        self._memory: OrderedDict[str, CacheEntry] = OrderedDict()
        self._stats = CacheStats()
        self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        if self.settings.enabled:
            self.settings.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(**self._stats.to_dict())

    def get(
        self,
        source_lang: str,
        target_lang: str,
        text: str,
    ) -> str | None:
        """Return a cached translation, or ``None`` on miss."""
        if not self.settings.enabled:
            with self._lock:
                self._stats.disabled_skips += 1
            return None

        text_hash = make_cache_key(source_lang, target_lang, text)

        with self._lock:
            entry = self._memory.get(text_hash)
            if entry is not None:
                if entry.is_expired(self.settings.ttl_seconds):
                    self._memory.pop(text_hash, None)
                    self._delete_disk_entry(text_hash)
                    self._stats.expired += 1
                else:
                    self._memory.move_to_end(text_hash)
                    self._stats.hits += 1
                    self._stats.memory_hits += 1
                    logger.debug(
                        "translation cache hit (memory)",
                        extra={
                            "event": "translation_cache_hit",
                            "layer": "memory",
                            "source_lang": source_lang,
                            "target_lang": target_lang,
                            "text_hash": text_hash,
                        },
                    )
                    return entry.translated_text

            entry = self._read_disk_entry(text_hash)
            if entry is None:
                self._stats.misses += 1
                logger.debug(
                    "translation cache miss",
                    extra={
                        "event": "translation_cache_miss",
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                        "text_hash": text_hash,
                    },
                )
                return None

            if entry.is_expired(self.settings.ttl_seconds):
                self._delete_disk_entry(text_hash)
                self._stats.expired += 1
                self._stats.misses += 1
                return None

            self._store_in_memory(text_hash, entry)
            self._stats.hits += 1
            self._stats.disk_hits += 1
            logger.debug(
                "translation cache hit (disk)",
                extra={
                    "event": "translation_cache_hit",
                    "layer": "disk",
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "text_hash": text_hash,
                },
            )
            return entry.translated_text

    def set(
        self,
        source_lang: str,
        target_lang: str,
        text: str,
        translated_text: str,
        *,
        provider: str = "sarvam",
        request_id: str | None = None,
    ) -> None:
        """Store a translation result in memory and on disk."""
        if not self.settings.enabled:
            with self._lock:
                self._stats.disabled_skips += 1
            return

        text_hash = make_cache_key(source_lang, target_lang, text)
        entry = CacheEntry(
            source_lang=source_lang,
            target_lang=target_lang,
            translated_text=translated_text,
            text_hash=text_hash,
            created_at=time.time(),
            provider=provider,
            request_id=request_id,
        )

        with self._lock:
            self._store_in_memory(text_hash, entry)
            self._write_disk_entry(text_hash, entry)
            self._stats.sets += 1
            logger.debug(
                "translation cache set",
                extra={
                    "event": "translation_cache_set",
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "text_hash": text_hash,
                    "provider": provider,
                    "request_id": request_id,
                },
            )

    def clear(self) -> None:
        """Remove all cached translations from memory and disk."""
        with self._lock:
            self._memory.clear()
            if self.settings.cache_dir.exists():
                for path in self.settings.cache_dir.rglob("*.json"):
                    path.unlink(missing_ok=True)
            logger.info(
                "translation cache cleared",
                extra={"event": "translation_cache_cleared"},
            )

    def purge_expired(self) -> int:
        """Delete expired entries. Returns the number of entries removed."""
        removed = 0
        with self._lock:
            expired_keys = [
                key
                for key, entry in self._memory.items()
                if entry.is_expired(self.settings.ttl_seconds)
            ]
            for key in expired_keys:
                self._memory.pop(key, None)
                self._delete_disk_entry(key)
                removed += 1

            if self.settings.cache_dir.exists():
                for path in self.settings.cache_dir.rglob("*.json"):
                    entry = self._read_disk_file(path)
                    if entry is None:
                        path.unlink(missing_ok=True)
                        removed += 1
                        continue
                    if entry.is_expired(self.settings.ttl_seconds):
                        path.unlink(missing_ok=True)
                        removed += 1

            self._stats.expired += removed
        if removed:
            logger.info(
                "translation cache purged expired entries",
                extra={
                    "event": "translation_cache_purge",
                    "removed": removed,
                },
            )
        return removed

    def _store_in_memory(self, text_hash: str, entry: CacheEntry) -> None:
        self._memory[text_hash] = entry
        self._memory.move_to_end(text_hash)
        while len(self._memory) > self.settings.max_memory_entries:
            evicted_hash, _ = self._memory.popitem(last=False)
            self._stats.evictions += 1
            logger.debug(
                "translation cache memory eviction",
                extra={
                    "event": "translation_cache_evict",
                    "text_hash": evicted_hash,
                },
            )

    def _disk_path(self, text_hash: str) -> Path:
        return self.settings.cache_dir / text_hash[:2] / f"{text_hash}.json"

    def _write_disk_entry(self, text_hash: str, entry: CacheEntry) -> None:
        path = self._disk_path(text_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _read_disk_entry(self, text_hash: str) -> CacheEntry | None:
        return self._read_disk_file(self._disk_path(text_hash))

    def _read_disk_file(self, path: Path) -> CacheEntry | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CacheEntry.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning(
                "translation cache entry unreadable; deleting",
                extra={
                    "event": "translation_cache_corrupt",
                    "path": str(path),
                },
            )
            path.unlink(missing_ok=True)
            return None

    def _delete_disk_entry(self, text_hash: str) -> None:
        path = self._disk_path(text_hash)
        path.unlink(missing_ok=True)


_default_cache: TranslationCache | None = None
_default_cache_lock = threading.Lock()


def get_translation_cache(settings: CacheSettings | None = None) -> TranslationCache:
    """Return the process-wide translation cache singleton."""
    global _default_cache
    if settings is not None:
        return TranslationCache(settings)

    if _default_cache is None:
        with _default_cache_lock:
            if _default_cache is None:
                _default_cache = TranslationCache()
    return _default_cache
