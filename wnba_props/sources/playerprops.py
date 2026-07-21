from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..cache import JsonCache
from ..config import Settings
from ..models import Game, PropLine
from ..utils import fetch_json, normalize_name, safe_float


STAT_TO_PROP_TYPE = {
    "Points": "PTS",
    "Rebounds": "REB",
    "Assists": "AST",
    "3-PT Made": "3PM",
    "Points + Rebounds + Assists": "PRA",
    "Points + Assists": "P+A",
    "Points + Rebounds": "P+R",
    "Rebounds + Assists": "R+A",
}

TEAM_ALIASES = {
    "LAS": "LA",
    "GSV": "GS",
    "PHO": "PHX",
    "WAS": "WSH",
}


class PlayerPropsSource:
    BASE_URL = "https://playerprops.ai/api/betprops/v2/predictor/event-predictions"

    def __init__(self, settings: Settings, lines_cache: JsonCache) -> None:
        self.settings = settings
        self.lines_cache = lines_cache
        self.failures: list[str] = []

    def fetch_prop_lines(self, games: Iterable[Game]) -> list[PropLine]:
        games = list(games)
        payload = self._fetch_payload()
        events = payload.get("eventPredictions", []) if isinstance(payload, dict) else []
        if not events:
            self.failures.append("PlayerProps payload did not include eventPredictions.")
            return []

        games_by_pair = {frozenset((game.home_team, game.away_team)): game for game in games}
        lines: list[PropLine] = []
        collected_at = datetime.now(timezone.utc)
        for event in events:
            event_teams = [self._normalize_team(team) for team in event.get("teams", [])]
            if len(event_teams) >= 2:
                game = games_by_pair.get(frozenset(event_teams[:2]))
            else:
                game = None
            if game is None:
                continue
            for player in event.get("players", []):
                player_name = player.get("playerName", "")
                team = self._normalize_team(player.get("team", ""))
                if not player_name or not team:
                    continue
                opponent = game.away_team if team == game.home_team else game.home_team
                stats = player.get("stats", {})
                if not isinstance(stats, dict):
                    continue
                for stat_name, stat_payload in stats.items():
                    prop_type = STAT_TO_PROP_TYPE.get(stat_name)
                    if prop_type not in self.settings.supported_prop_types:
                        continue
                    if not isinstance(stat_payload, dict):
                        continue
                    play = self._book_play(stat_payload.get("plays", []))
                    if play is None:
                        continue
                    line_value = safe_float(play.get("line"), default=-1.0)
                    if line_value < 0:
                        continue
                    lines.append(
                        PropLine(
                            event_id=game.game_id,
                            game_date=game.game_date,
                            player_name_raw=player_name,
                            player_name_norm=normalize_name(player_name),
                            team=team,
                            opponent=opponent,
                            prop_type=prop_type,
                            line=line_value,
                            bookmaker=self.settings.playerprops_book.lower(),
                            source="playerprops_ai",
                            collected_at=collected_at,
                        )
                    )
        return self._dedupe(lines)

    def _fetch_payload(self) -> dict:
        cache_key = f"playerprops_wnba_{self.settings.screen_date.isoformat()}"
        cached = self.lines_cache.get(cache_key)
        if cached is not None:
            return cached

        date_from, date_to = self._utc_window()
        params = urlencode(
            {
                "dateFrom": date_from,
                "dateTo": date_to,
                "league": "WNBA",
                "isPremium": "false",
            }
        )
        payload = fetch_json(f"{self.BASE_URL}?{params}", headers={"Accept": "application/json"}, timeout=30)
        self.lines_cache.set(cache_key, payload)
        return payload

    def _utc_window(self) -> tuple[str, str]:
        local_tz = ZoneInfo("America/Detroit")
        start_local = datetime.combine(self.settings.screen_date, time.min, tzinfo=local_tz)
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        end_utc = end_local.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return start_utc, end_utc

    def _book_play(self, plays: object) -> dict | None:
        if not isinstance(plays, list):
            return None
        for play in plays:
            if not isinstance(play, dict):
                continue
            if str(play.get("source", "")).upper() == self.settings.playerprops_book:
                return play
        return None

    def _normalize_team(self, value: str) -> str:
        cleaned = value.strip().upper()
        return TEAM_ALIASES.get(cleaned, cleaned)

    def _dedupe(self, lines: list[PropLine]) -> list[PropLine]:
        deduped: dict[tuple[str, str, str], PropLine] = {}
        for line in lines:
            deduped[(line.player_name_norm, line.team, line.prop_type)] = line
        return sorted(deduped.values(), key=lambda line: (line.team, line.player_name_raw, line.prop_type))
