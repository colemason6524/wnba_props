from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..cache import JsonCache
from ..config import ESPN_TO_TEAM_ABBR
from ..models import Game
from ..utils import fetch_json, normalize_name, parse_iso_datetime, safe_int


@dataclass
class ShadowGameContext:
    away_team: str
    home_team: str
    away_spread: float
    home_spread: float
    total: float
    provider: str


@dataclass(frozen=True)
class ShadowGameStatus:
    game_id: str
    state: str
    completed: bool
    detail: str


@dataclass(frozen=True)
class ShadowBoxscoreStatLine:
    player_name_raw: str
    player_name_norm: str
    team: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    threes_made: int


class ShadowEspnSlateSource:
    """ESPN slate reader with headers isolated from the production source."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
    HEADERS = {
        "Accept": "application/json",
        "User-Agent": "curl/8.7.1",
    }

    def __init__(self) -> None:
        self.game_contexts: dict[tuple[str, str], ShadowGameContext] = {}
        self.game_statuses: dict[str, ShadowGameStatus] = {}

    def fetch_games(self, screen_date: date) -> list[Game]:
        payload = fetch_json(
            self.SCOREBOARD_URL.format(date_str=screen_date.strftime("%Y%m%d")),
            headers=self.HEADERS,
        )
        games = self.parse_games(payload, screen_date)
        self.game_contexts = self.parse_game_contexts(payload)
        self.game_statuses = self.parse_game_statuses(payload)
        return games

    @staticmethod
    def parse_games(payload: dict, screen_date: date) -> list[Game]:
        games: list[Game] = []
        for event in payload.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            competition = competitions[0]
            home_team = ""
            away_team = ""
            for competitor in competition.get("competitors", []):
                team_payload = competitor.get("team", {})
                team_name = team_payload.get("displayName", "")
                team_abbr = ESPN_TO_TEAM_ABBR.get(team_name, team_payload.get("abbreviation", ""))
                if competitor.get("homeAway") == "home":
                    home_team = team_abbr
                else:
                    away_team = team_abbr
            if not home_team or not away_team:
                continue
            games.append(
                Game(
                    game_id=str(event["id"]),
                    game_date=screen_date,
                    game_time=parse_iso_datetime(event["date"]),
                    home_team=home_team,
                    away_team=away_team,
                    source="espn_shadow",
                )
            )
        return games

    @staticmethod
    def parse_game_contexts(payload: dict) -> dict[tuple[str, str], ShadowGameContext]:
        contexts: dict[tuple[str, str], ShadowGameContext] = {}
        for event in payload.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            competition = competitions[0]
            team_by_side: dict[str, str] = {}
            for competitor in competition.get("competitors", []):
                side = competitor.get("homeAway", "")
                team_payload = competitor.get("team", {})
                display_name = team_payload.get("displayName", "")
                team_by_side[side] = ESPN_TO_TEAM_ABBR.get(
                    display_name,
                    team_payload.get("abbreviation", ""),
                )
            home = team_by_side.get("home", "")
            away = team_by_side.get("away", "")
            odds_entries = competition.get("odds", [])
            if not home or not away or not odds_entries:
                continue
            odds = odds_entries[0]
            total = _optional_float(odds.get("overUnder"))
            point_spread = odds.get("pointSpread", {})
            home_spread = _optional_float(
                point_spread.get("home", {}).get("close", {}).get("line")
            )
            away_spread = _optional_float(
                point_spread.get("away", {}).get("close", {}).get("line")
            )
            if home_spread is None:
                home_spread = _optional_float(odds.get("spread"))
            if away_spread is None and home_spread is not None:
                away_spread = -home_spread
            if total is None or home_spread is None or away_spread is None:
                continue
            provider = str(odds.get("provider", {}).get("name", "")).strip()
            contexts[(away, home)] = ShadowGameContext(
                away_team=away,
                home_team=home,
                away_spread=away_spread,
                home_spread=home_spread,
                total=total,
                provider=provider,
            )
        return contexts

    @staticmethod
    def parse_game_statuses(payload: dict) -> dict[str, ShadowGameStatus]:
        statuses: dict[str, ShadowGameStatus] = {}
        for event in payload.get("events", []):
            game_id = str(event.get("id", "")).strip()
            if not game_id:
                continue
            status_type = (event.get("status", {}) or {}).get("type", {}) or {}
            statuses[game_id] = ShadowGameStatus(
                game_id=game_id,
                state=str(status_type.get("state", "")).strip().lower(),
                completed=bool(status_type.get("completed", False)),
                detail=str(status_type.get("detail", "")).strip(),
            )
        return statuses


class ShadowEspnBoxscoreSource:
    """Final boxscore reader isolated from the production ESPN client/cache."""

    SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event={event_id}"
    HEADERS = ShadowEspnSlateSource.HEADERS

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_boxscore(self, event_id: str) -> dict[tuple[str, str], ShadowBoxscoreStatLine]:
        cache_key = f"espn_final_boxscore_{event_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return {
                (item["player_name_norm"], item["team"]): ShadowBoxscoreStatLine(**item)
                for item in cached
            }

        payload = fetch_json(
            self.SUMMARY_URL.format(event_id=event_id),
            headers=self.HEADERS,
        )
        parsed = self.parse_boxscore(payload)
        if parsed:
            self.cache.set(cache_key, [stat_line.__dict__ for stat_line in parsed.values()])
        return parsed

    @staticmethod
    def parse_boxscore(payload: dict) -> dict[tuple[str, str], ShadowBoxscoreStatLine]:
        parsed: dict[tuple[str, str], ShadowBoxscoreStatLine] = {}
        for team_block in payload.get("boxscore", {}).get("players", []):
            team_info = team_block.get("team", {}) or {}
            team_name = str(team_info.get("displayName", "")).strip()
            team_abbr = ESPN_TO_TEAM_ABBR.get(
                team_name,
                str(team_info.get("abbreviation", "")).strip(),
            )
            if not team_abbr:
                continue

            for stat_group in team_block.get("statistics", []):
                labels = [str(label).strip().upper() for label in stat_group.get("labels", [])]
                if "MIN" not in labels or "PTS" not in labels:
                    continue
                for athlete_row in stat_group.get("athletes", []):
                    athlete = athlete_row.get("athlete", {}) or {}
                    player_name = str(athlete.get("displayName", "")).strip()
                    if not player_name:
                        continue
                    values = athlete_row.get("stats", []) or []
                    stat_map = {
                        label: values[index]
                        for index, label in enumerate(labels)
                        if index < len(values)
                    }
                    stat_line = ShadowBoxscoreStatLine(
                        player_name_raw=player_name,
                        player_name_norm=normalize_name(player_name),
                        team=team_abbr,
                        minutes=_parse_minutes(stat_map.get("MIN", "0")),
                        points=safe_int(stat_map.get("PTS")),
                        rebounds=safe_int(stat_map.get("REB")),
                        assists=safe_int(stat_map.get("AST")),
                        threes_made=_parse_threes_made(stat_map.get("3PT", "0")),
                    )
                    parsed.setdefault((stat_line.player_name_norm, stat_line.team), stat_line)
        return parsed


def _optional_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_minutes(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        try:
            return int(minutes) + int(seconds) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_threes_made(value: object) -> int:
    text = str(value or "").strip()
    if "-" in text:
        text = text.split("-", 1)[0]
    return safe_int(text)
