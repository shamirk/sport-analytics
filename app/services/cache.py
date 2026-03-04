"""In-memory TTL cache keyed by arbitrary string keys."""

from __future__ import annotations

import os
import time
from typing import Any

CACHE_TTL: float = float(os.environ.get("CACHE_TTL", "86400"))


class TTLCache:
    """Simple dict-based cache with per-entry expiry timestamps."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry["expires_at"]:
            del self._store[key]
            return None
        return entry["value"]

    def set(self, key: str, value: Any, ttl: float = CACHE_TTL) -> None:
        self._store[key] = {"value": value, "expires_at": time.monotonic() + ttl}

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# Module-level singleton used across the app
cache = TTLCache()
