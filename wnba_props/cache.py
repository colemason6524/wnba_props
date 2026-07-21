from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .utils import ensure_dir


class JsonCache:
    def __init__(self, root: Path, ttl_hours: float = 6) -> None:
        self.root = root
        self.ttl = timedelta(hours=ttl_hours)
        ensure_dir(root)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def _read_payload(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def get(self, key: str) -> Any | None:
        payload = self._read_payload(key)
        if payload is None:
            return None
        saved_at = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved_at > self.ttl:
            return None
        return payload["data"]

    def get_stale(self, key: str) -> Any | None:
        payload = self._read_payload(key)
        if payload is None:
            return None
        return payload["data"]

    def get_payload(self, key: str) -> dict[str, Any] | None:
        return self._read_payload(key)

    def set(self, key: str, data: Any) -> None:
        path = self._path(key)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
