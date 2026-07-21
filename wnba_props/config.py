from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
CONFIG_DIR = ROOT / "config"
OUTPUTS_DIR = ROOT / "outputs"


SUPPORTED_PROP_TYPES = [
    "PTS",
    "REB",
    "AST",
    "PRA",
    "P+A",
    "P+R",
    "R+A",
    "3PM",
]

DEFAULT_PROP_TYPES = [
    "PTS",
    "REB",
    "AST",
    "3PM",
]

ESPN_TO_TEAM_ABBR = {
    "Atlanta Dream": "ATL",
    "Chicago Sky": "CHI",
    "Connecticut Sun": "CON",
    "Dallas Wings": "DAL",
    "Golden State Valkyries": "GS",
    "Indiana Fever": "IND",
    "Las Vegas Aces": "LV",
    "Los Angeles Sparks": "LA",
    "Minnesota Lynx": "MIN",
    "New York Liberty": "NY",
    "Phoenix Mercury": "PHX",
    "Portland Fire": "POR",
    "Seattle Storm": "SEA",
    "Toronto Tempo": "TOR",
    "Washington Mystics": "WSH",
}

TEAM_ABBR_TO_ESPN_ID = {
    "ATL": "20",
    "CHI": "19",
    "CON": "18",
    "DAL": "3",
    "GS": "129689",
    "IND": "5",
    "LA": "6",
    "LV": "17",
    "MIN": "8",
    "NY": "9",
    "PHX": "11",
    "POR": "132052",
    "SEA": "14",
    "TOR": "131935",
    "WSH": "16",
}

TEAM_ABBR_TO_FANDUEL_SLUG = {
    "ATL": "atlanta-dream",
    "CHI": "chicago-sky",
    "CON": "connecticut-sun",
    "DAL": "dallas-wings",
    "GS": "golden-state-valkyries",
    "IND": "indiana-fever",
    "LA": "los-angeles-sparks",
    "LV": "las-vegas-aces",
    "MIN": "minnesota-lynx",
    "NY": "new-york-liberty",
    "PHX": "phoenix-mercury",
    "POR": "portland-fire",
    "SEA": "seattle-storm",
    "TOR": "toronto-tempo",
    "WSH": "washington-mystics",
}


@dataclass
class Thresholds:
    primary_hits_last_5: int = 4
    support_hits_last_10: int = 7
    min_delta_avg_last_5: float = 1.0
    low_minutes_warning: float = 22.0
    strong_minutes_threshold: float = 28.0
    min_played_last_10: int = 3


@dataclass
class Settings:
    screen_date: date = field(default_factory=date.today)
    cache_ttl_hours: int = 24
    lines_cache_ttl_minutes: int = 10
    injuries_cache_ttl_minutes: int = 10
    supported_prop_types: List[str] = field(default_factory=lambda: DEFAULT_PROP_TYPES.copy())
    thresholds: Thresholds = field(default_factory=Thresholds)
    player_aliases: Dict[str, str] = field(default_factory=dict)
    line_source: str = "playerprops"
    playerprops_book: str = "FANDUEL"
    fanduel_event_urls: List[str] = field(default_factory=list)
    include_under_candidates: bool = True
    pregame_only: bool = True
    sticky_daily_log_cache: bool = True
    export_history: bool = False
    min_display_score: int = 7
    send_discord: bool = False
    discord_webhook_url: str = ""
    discord_min_score: int = 8
    discord_limit: int = 8


def load_settings() -> Settings:
    screen_date_str = os.environ.get("SCREEN_DATE", "").strip()
    screen_date = date.today()
    if screen_date_str:
        screen_date = datetime.strptime(screen_date_str, "%Y-%m-%d").date()

    prop_types_str = os.environ.get("SCREEN_PROP_TYPES", "").strip()
    supported_prop_types = DEFAULT_PROP_TYPES.copy()
    if prop_types_str:
        requested = [item.strip().upper() for item in prop_types_str.split(",") if item.strip()]
        supported_prop_types = [item for item in SUPPORTED_PROP_TYPES if item in requested]

    aliases_path = CONFIG_DIR / "player_aliases.json"
    aliases = {}
    if aliases_path.exists():
        aliases = json.loads(aliases_path.read_text())

    fanduel_event_urls = [
        item.strip()
        for item in os.environ.get("FANDUEL_EVENT_URLS", "").split(",")
        if item.strip()
    ]

    return Settings(
        screen_date=screen_date,
        cache_ttl_hours=int(os.environ.get("CACHE_TTL_HOURS", "24")),
        lines_cache_ttl_minutes=int(os.environ.get("LINES_CACHE_TTL_MINUTES", "10")),
        injuries_cache_ttl_minutes=int(os.environ.get("INJURIES_CACHE_TTL_MINUTES", "10")),
        supported_prop_types=supported_prop_types,
        player_aliases=aliases,
        line_source=os.environ.get("LINE_SOURCE", "playerprops").strip().lower(),
        playerprops_book=os.environ.get("PLAYERPROPS_BOOK", "FANDUEL").strip().upper(),
        fanduel_event_urls=fanduel_event_urls,
        include_under_candidates=os.environ.get("INCLUDE_UNDERS", "true").strip().lower() not in {"0", "false", "no"},
        pregame_only=os.environ.get("PREGAME_ONLY", "true").strip().lower() not in {"0", "false", "no"},
        sticky_daily_log_cache=os.environ.get("STICKY_DAILY_LOG_CACHE", "true").strip().lower() not in {"0", "false", "no"},
        export_history=os.environ.get("EXPORT_HISTORY", "false").strip().lower() in {"1", "true", "yes"},
        min_display_score=int(os.environ.get("MIN_DISPLAY_SCORE", "7")),
        send_discord=os.environ.get("SEND_DISCORD", "false").strip().lower() in {"1", "true", "yes"},
        discord_webhook_url=(
            os.environ.get("WNBA_PROPS_DISCORD_WEBHOOK_URL", "").strip()
            or os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        ),
        discord_min_score=int(os.environ.get("DISCORD_MIN_SCORE", "8")),
        discord_limit=int(os.environ.get("DISCORD_LIMIT", "8")),
    )
