from __future__ import annotations

from dataclasses import dataclass

from ..cache import JsonCache
from ..config import ESPN_TO_TEAM_ABBR
from ..utils import fetch_json, normalize_name, safe_int


@dataclass
class EspnBoxscoreStatLine:
    player_name_raw: str
    player_name_norm: str
    team: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    threes_made: int


class EspnBoxscoreSource:
    SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event={event_id}"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_boxscore(self, event_id: str) -> dict[tuple[str, str], EspnBoxscoreStatLine]:
        cache_key = f"espn_boxscore_{event_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return {
                (item["player_name_norm"], item["team"]): EspnBoxscoreStatLine(**item)
                for item in cached
            }

        payload = fetch_json(self.SUMMARY_URL.format(event_id=event_id))
        parsed = self._parse_boxscore(payload)
        self.cache.set(cache_key, [stat_line.__dict__ for stat_line in parsed.values()])
        return parsed

    def _parse_boxscore(self, payload: dict) -> dict[tuple[str, str], EspnBoxscoreStatLine]:
        parsed: dict[tuple[str, str], EspnBoxscoreStatLine] = {}
        for team_block in payload.get("boxscore", {}).get("players", []):
            team_info = team_block.get("team", {}) or {}
            team_name = team_info.get("displayName", "")
            team_abbr = ESPN_TO_TEAM_ABBR.get(team_name, team_info.get("abbreviation", ""))
            if not team_abbr:
                continue

            for stat_group in team_block.get("statistics", []):
                labels = [str(label).strip().upper() for label in stat_group.get("labels", [])]
                if not labels:
                    continue
                for athlete_row in stat_group.get("athletes", []):
                    athlete = athlete_row.get("athlete", {}) or {}
                    player_name = athlete.get("displayName", "").strip()
                    if not player_name:
                        continue
                    values = athlete_row.get("stats", []) or []
                    stat_map = {
                        label: values[index]
                        for index, label in enumerate(labels)
                        if index < len(values)
                    }
                    stat_line = EspnBoxscoreStatLine(
                        player_name_raw=player_name,
                        player_name_norm=normalize_name(player_name),
                        team=team_abbr,
                        minutes=self._parse_minutes(stat_map.get("MIN", "0")),
                        points=safe_int(stat_map.get("PTS")),
                        rebounds=safe_int(stat_map.get("REB")),
                        assists=safe_int(stat_map.get("AST")),
                        threes_made=self._parse_threes_made(stat_map.get("3PT", "0")),
                    )
                    parsed[(stat_line.player_name_norm, stat_line.team)] = stat_line
        return parsed

    def _parse_minutes(self, value: object) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        if ":" in text:
            parts = text.split(":")
            if len(parts) == 2:
                try:
                    return int(parts[0]) + int(parts[1]) / 60.0
                except ValueError:
                    return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _parse_threes_made(self, value: object) -> int:
        text = str(value or "").strip()
        if "-" in text:
            text = text.split("-", 1)[0]
        return safe_int(text)
