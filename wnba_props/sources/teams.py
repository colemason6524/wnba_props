"""Canonical WNBA team IDs and cross-source normalization.

Canonical IDs are the abbreviations used throughout the project (e.g. "NY",
"LV", "GS"). Sources spell teams differently ("Las Vegas Aces", "Aces",
"LVA"), so every market source routes names through ``normalize_team``.
A failed lookup returns ``None`` and is surfaced in diagnostics rather than
force-matched.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from ..config import ESPN_TO_TEAM_ABBR


CANONICAL_TEAMS = frozenset(ESPN_TO_TEAM_ABBR.values())


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", text.upper())
    return re.sub(r"\s+", " ", text).strip()


def _nickname(full_name: str) -> str:
    return full_name.split()[-1]


_ALIASES: dict[str, str] = {}
for _display, _abbr in ESPN_TO_TEAM_ABBR.items():
    _ALIASES[_clean(_display)] = _abbr
    _ALIASES[_clean(_abbr)] = _abbr
    _ALIASES[_clean(_nickname(_display))] = _abbr

# City / common cross-source spellings.
_ALIASES.update(
    {
        _clean("Las Vegas"): "LV",
        _clean("Golden State"): "GS",
        _clean("Los Angeles"): "LA",
        _clean("New York"): "NY",
        _clean("Washington"): "WSH",
        _clean("Connecticut"): "CON",
        _clean("Phoenix"): "PHX",
        _clean("Minnesota"): "MIN",
        _clean("Portland"): "POR",
        _clean("Toronto"): "TOR",
        _clean("Seattle"): "SEA",
        _clean("Atlanta"): "ATL",
        _clean("Chicago"): "CHI",
        _clean("Dallas"): "DAL",
        _clean("Indiana"): "IND",
        _clean("LAS"): "LA",
        _clean("LVA"): "LV",
        _clean("NYL"): "NY",
        _clean("GSV"): "GS",
        _clean("PDX"): "POR",
        _clean("PHO"): "PHX",
        _clean("WAS"): "WSH",
        _clean("CONN"): "CON",
        _clean("MINN"): "MIN",
        _clean("GSW"): "GS",
        _clean("LA SPARKS"): "LA",
        _clean("VEGAS"): "LV",
    }
)
_ALIASES_CLEAN = {k: v for k, v in _ALIASES.items() if k}


def normalize_team(raw: object) -> Optional[str]:
    """Map any source spelling to a canonical WNBA abbreviation, or None."""
    if raw is None:
        return None
    cleaned = _clean(str(raw))
    if not cleaned:
        return None
    if cleaned in _ALIASES_CLEAN:
        return _ALIASES_CLEAN[cleaned]
    # Trailing mascot/qualifier words ("Aces WNBA", "Liberty (NY)").
    words = cleaned.split(" ")
    for cut in range(len(words) - 1, 0, -1):
        shorter = " ".join(words[:cut])
        if shorter in _ALIASES_CLEAN:
            return _ALIASES_CLEAN[shorter]
    # Last-word nickname match ("... Sun").
    if words[-1] in _ALIASES_CLEAN and len(words[-1]) >= 3:
        return _ALIASES_CLEAN[words[-1]]
    return None
