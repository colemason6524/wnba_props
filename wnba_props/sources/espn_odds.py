from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..cache import JsonCache
from ..utils import fetch_text


ESPN_ALIAS_TO_ABBR = {
    "atl": "ATL",
    "chi": "CHI",
    "con": "CON",
    "dal": "DAL",
    "gs": "GS",
    "ind": "IND",
    "la": "LA",
    "lv": "LV",
    "min": "MIN",
    "ny": "NY",
    "phx": "PHX",
    "por": "POR",
    "sea": "SEA",
    "tor": "TOR",
    "wsh": "WSH",
}


@dataclass
class GameOddsContext:
    away_team: str
    home_team: str
    away_spread: float
    home_spread: float
    total: float


class EspnOddsSource:
    ODDS_URL = "https://www.espn.com/wnba/odds/_/date/{date_str}"
    CACHE_VERSION = "v3"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_game_context(self, screen_date: date) -> dict[tuple[str, str], GameOddsContext]:
        cache_key = f"espn_odds_{self.CACHE_VERSION}_{screen_date.isoformat()}"
        cached = self.cache.get(cache_key)
        if cached:
            return {tuple(item["key"]): GameOddsContext(**item["value"]) for item in cached}

        html = fetch_text(self.ODDS_URL.format(date_str=screen_date.strftime("%Y%m%d")))
        contexts = self._parse_html(html)
        serializable = [{"key": list(key), "value": value.__dict__} for key, value in contexts.items()]
        self.cache.set(cache_key, serializable)
        return contexts

    def _parse_html(self, html: str) -> dict[tuple[str, str], GameOddsContext]:
        results: dict[tuple[str, str], GameOddsContext] = {}
        markers = list(re.finditer(r'data-testid="betSixPack-\d+"', html))
        for index, marker in enumerate(markers):
            start = marker.start()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(html)
            block = html[start:end]
            team_aliases = re.findall(r'/wnba/team/_/name/([a-z]+)/', block)
            spreads = re.findall(r'data-track-event_detail="pointSpread [^"]*".*?<div class="nfCSQ tDrA CNnbD ">([+-]\d+\.\d)</div>', block, flags=re.DOTALL)
            totals = re.findall(r'data-track-event_detail="total [^"]*".*?<div class="nfCSQ tDrA CNnbD ">[ou](\d+\.\d)</div>', block, flags=re.DOTALL)
            if len(team_aliases) < 2 or len(spreads) < 2 or not totals:
                continue
            away = ESPN_ALIAS_TO_ABBR.get(team_aliases[0], team_aliases[0].upper())
            home = ESPN_ALIAS_TO_ABBR.get(team_aliases[1], team_aliases[1].upper())
            results[(away, home)] = GameOddsContext(
                away_team=away,
                home_team=home,
                away_spread=float(spreads[0]),
                home_spread=float(spreads[1]),
                total=float(totals[0]),
            )
        return results
