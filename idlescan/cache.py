"""On-disk cache for slow-changing lookups (token metadata, prices).

Kept deliberately simple: one JSON file, atomic replace, TTL per entry.  A cache
miss or a corrupt file is never fatal - it just costs an extra RPC call.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "idlescan"


class Cache:
    def __init__(self, directory: Path | str | None = None, *, enabled: bool = True):
        self.enabled = enabled
        self.directory = Path(directory) if directory else default_cache_dir()
        self.path = self.directory / "cache.json"
        self._entries: dict[str, Any] = {}
        self._dirty = False
        if not enabled:
            return
        try:
            raw = json.loads(self.path.read_text())
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                self._entries = raw["entries"]
        except (OSError, ValueError):
            self._entries = {}

    def get(self, key: str, *, max_age: float | None = None) -> Any | None:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if not isinstance(entry, dict) or "value" not in entry:
            return None
        if max_age is not None:
            stored = float(entry.get("at", 0))
            if time.time() - stored > max_age:
                return None
        return entry["value"]

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._entries[key] = {"at": time.time(), "value": value}
        self._dirty = True

    def flush(self) -> None:
        if not self.enabled or not self._dirty:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"version": 1, "entries": self._entries})
            handle, temp_path = tempfile.mkstemp(dir=str(self.directory), suffix=".tmp")
            with os.fdopen(handle, "w") as fh:
                fh.write(payload)
            os.replace(temp_path, self.path)
            self._dirty = False
        except OSError:
            pass  # a read-only cache directory must never break a scan
