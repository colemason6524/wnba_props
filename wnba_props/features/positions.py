from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping, Optional

from ..cache import JsonCache
from ..config import TEAM_ABBR_TO_ESPN_ID
from ..utils import fetch_espn_json, normalize_name


# Coarse position classes used for opponent positional defense. Combo positions
# collapse to the more perimeter-oriented class so guards and wings share a
# bucket while true bigs stay separate.
_GUARD = "G"
_FORWARD = "F"
_CENTER = "C"

_COMBO_CLASSES = {
    "PG": _GUARD,
    "SG": _GUARD,
    "SF": _FORWARD,
    "PF": _FORWARD,
    "G": _GUARD,
    "F": _FORWARD,
    "C": _CENTER,
    "GUARD": _GUARD,
    "FORWARD": _FORWARD,
    "CENTER": _CENTER,
}

ROSTER_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{team_id}/roster"
)


def position_class(raw: str) -> str:
    """Map a raw position string (``G``, ``G-F``, ``Forward``) to ``G``/``F``/``C``."""
    text = (raw or "").strip().upper()
    if not text:
        return ""
    if text in _COMBO_CLASSES:
        return _COMBO_CLASSES[text]
    for token in text.replace("-", " ").replace("/", " ").split():
        if token in _COMBO_CLASSES:
            return _COMBO_CLASSES[token]
    return ""


def load_position_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def merge_position_maps(*maps: Optional[Mapping[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for mapping in maps:
        if not mapping:
            continue
        for key, value in mapping.items():
            if value:
                merged[str(key)] = str(value)
    return merged


def _positions_from_roster(payload: Mapping) -> dict[str, str]:
    positions: dict[str, str] = {}
    for athlete in payload.get("athletes", []) or []:
        name = str(athlete.get("fullName", "")).strip()
        if not name:
            continue
        raw = ""
        position = athlete.get("position")
        if isinstance(position, Mapping):
            raw = str(position.get("abbreviation") or position.get("name") or "")
        elif position:
            raw = str(position)
        cls = position_class(raw)
        if cls:
            positions[normalize_name(name)] = cls
    return positions


def fetch_position_map(
    teams: Iterable[str],
    cache: JsonCache,
) -> dict[str, str]:
    """Position classes for every rostered player on the given teams.

    Uses the ESPN roster API with the shared cache. Failures for a single team
    are skipped so positional defense degrades to team-level rather than
    blocking the board.
    """
    positions: dict[str, str] = {}
    for team in sorted(set(teams)):
        team_id = TEAM_ABBR_TO_ESPN_ID.get(team)
        if not team_id:
            continue
        cache_key = f"espn_roster_positions_{team_id}"
        payload = cache.get(cache_key)
        if payload is None:
            try:
                payload = fetch_espn_json(ROSTER_URL.format(team_id=team_id))
            except Exception:  # noqa: BLE001 - missing roster just skips positions
                continue
            cache.set(cache_key, payload)
        positions.update(_positions_from_roster(payload))
    return positions
