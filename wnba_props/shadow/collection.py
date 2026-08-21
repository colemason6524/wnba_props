from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..models import Game


@dataclass(frozen=True)
class CaptureWindow:
    minimum_lead_minutes: float = 20.0
    maximum_lead_minutes: float = 90.0

    def __post_init__(self) -> None:
        if self.minimum_lead_minutes < 0.0:
            raise ValueError("minimum capture lead must be non-negative")
        if self.maximum_lead_minutes < self.minimum_lead_minutes:
            raise ValueError("maximum capture lead must be at least the minimum lead")


def capture_lead_minutes(game_time: datetime, collected_at: datetime) -> float:
    return round((game_time - collected_at).total_seconds() / 60.0, 2)


def select_games_in_capture_window(
    games: Iterable[Game],
    *,
    now: datetime,
    window: CaptureWindow,
) -> list[Game]:
    return sorted(
        [
            game
            for game in games
            if window.minimum_lead_minutes
            <= capture_lead_minutes(game.game_time, now)
            <= window.maximum_lead_minutes
        ],
        key=lambda game: (game.game_time, game.game_id),
    )


def projection_id(projection: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(projection.get("model_version", "")),
            str(projection.get("game_id", "")),
            str(projection.get("player_name_norm", "")),
            str(projection.get("prop_type", "")),
            str(projection.get("line", "")),
            str(projection.get("bookmaker", "")),
            str(projection.get("line_collected_at", "")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class CaptureRegistry:
    """Shadow-only state separating material attempts from completed captures.

    An attempt is recorded for every run that entered the capture path, even
    partial or failed ones, so later no-op runs cannot hide an eligible-window
    failure. A game is marked complete only when all of its fetched PTS lines
    received a projection or a deterministic exclusion.
    """

    SCHEMA_VERSION = 2

    def __init__(self, path: Path) -> None:
        self.path = path
        self._payload = self._load()

    def is_captured(self, *, game_id: str, model_version: str, line_source: str) -> bool:
        return self._key(game_id, model_version, line_source) in self._payload["completed"]

    def record_attempt(
        self,
        *,
        games: Iterable[Game],
        model_version: str,
        line_source: str,
        snapshot_path: Path | None,
        captured_at: datetime,
        outcomes: dict[str, str],
    ) -> None:
        attempt = {
            "attempted_at": captured_at.isoformat(),
            "model_version": model_version,
            "line_source": line_source,
            "snapshot_path": str(snapshot_path.resolve()) if snapshot_path else None,
            "games": [
                {
                    "game_id": game.game_id,
                    "game_time": game.game_time.isoformat(),
                    "capture_lead_minutes": capture_lead_minutes(game.game_time, captured_at),
                    "outcome": outcomes.get(game.game_id, "unknown"),
                }
                for game in games
            ],
        }
        self._payload["attempts"].append(attempt)
        self._write()

    def mark_complete(
        self,
        *,
        games: Iterable[Game],
        model_version: str,
        line_source: str,
        snapshot_path: Path,
        captured_at: datetime,
    ) -> None:
        for game in games:
            self._payload["completed"][self._key(game.game_id, model_version, line_source)] = {
                "game_id": game.game_id,
                "game_time": game.game_time.isoformat(),
                "model_version": model_version,
                "line_source": line_source,
                "captured_at": captured_at.isoformat(),
                "capture_lead_minutes": capture_lead_minutes(game.game_time, captured_at),
                "snapshot_path": str(snapshot_path.resolve()),
            }
        self._write()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "attempts": [], "completed": {}}
        payload = json.loads(self.path.read_text())
        schema_version = payload.get("schema_version")
        if schema_version == 1:
            return {
                "schema_version": self.SCHEMA_VERSION,
                "attempts": [],
                "completed": payload.get("captures", {}),
            }
        if schema_version != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported capture registry schema: {self.path}")
        if not isinstance(payload.get("completed"), dict):
            raise ValueError(f"invalid capture registry: {self.path}")
        return {
            "schema_version": self.SCHEMA_VERSION,
            "attempts": payload.get("attempts", []),
            "completed": payload.get("completed", {}),
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(self._payload, indent=2, sort_keys=True))
        temporary_path.replace(self.path)

    @staticmethod
    def _key(game_id: str, model_version: str, line_source: str) -> str:
        return "|".join([model_version, line_source, game_id])
