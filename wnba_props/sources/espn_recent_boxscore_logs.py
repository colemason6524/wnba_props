from __future__ import annotations

from datetime import date, timedelta

from ..cache import JsonCache
from ..models import PlayerGameLog
from ..utils import normalize_name
from .espn import EspnSlateSource
from .espn_boxscore import EspnBoxscoreSource

# Postseason bounds: one scoreboard request per date in the window, shared by
# every subject in the run, instead of a per-player lookback scan.
DEFAULT_LOOKBACK_DAYS = 21
DEFAULT_MAX_GAMES = 10
MAX_CONSECUTIVE_SLATE_FAILURES = 3


def _candidate_norms(player_name: str) -> list[str]:
    normalized = normalize_name(player_name)
    candidates = {normalized}
    if normalized.endswith(" jr"):
        candidates.add(normalized[:-3].strip())
    else:
        candidates.add(f"{normalized} jr")
    if normalized.endswith(" sr"):
        candidates.add(normalized[:-3].strip())
    else:
        candidates.add(f"{normalized} sr")
    return [candidate for candidate in candidates if candidate]


def _did_play(stat_line: object) -> bool:
    return getattr(stat_line, "minutes", 0.0) > 0 or any(
        getattr(stat_line, attribute, 0) > 0
        for attribute in ("points", "rebounds", "assists", "threes_made")
    )


class EspnRecentBoxscoreLogsSource:
    def __init__(self, cache: JsonCache) -> None:
        self.slate_source = EspnSlateSource()
        self.boxscore_source = EspnBoxscoreSource(cache)

    def fetch_recent_logs(
        self,
        player_name: str,
        team_abbr: str,
        end_date: date,
        max_games: int = 12,
        lookback_days: int = 45,
    ) -> list[PlayerGameLog]:
        player_norms = self._candidate_norms(player_name)
        logs: list[PlayerGameLog] = []

        for days_back in range(1, lookback_days + 1):
            game_date = end_date - timedelta(days=days_back)
            try:
                games = self.slate_source.fetch_games(game_date)
            except Exception:
                continue

            target_game = None
            for game in games:
                if team_abbr in {game.home_team, game.away_team}:
                    target_game = game
                    break
            if target_game is None:
                continue

            try:
                stat_lines = self.boxscore_source.fetch_boxscore(target_game.game_id)
            except Exception:
                continue

            stat_line = None
            for player_norm in player_norms:
                stat_line = stat_lines.get((player_norm, team_abbr))
                if stat_line is not None:
                    break
            if stat_line is None:
                continue

            opponent = target_game.away_team if target_game.home_team == team_abbr else target_game.home_team
            logs.append(
                PlayerGameLog(
                    player_name_raw=stat_line.player_name_raw,
                    player_name_norm=stat_line.player_name_norm,
                    game_date=game_date,
                    team=team_abbr,
                    opponent=opponent,
                    minutes=stat_line.minutes,
                    points=stat_line.points,
                    rebounds=stat_line.rebounds,
                    assists=stat_line.assists,
                    threes_made=stat_line.threes_made,
                    did_play=stat_line.minutes > 0
                    or any(value > 0 for value in (stat_line.points, stat_line.rebounds, stat_line.assists, stat_line.threes_made)),
                    source="espn_boxscore_logs",
                )
            )
            if len(logs) >= max_games:
                break

        logs.sort(key=lambda item: item.game_date, reverse=True)
        return logs

    def _candidate_norms(self, player_name: str) -> list[str]:
        return _candidate_norms(player_name)


class EspnBoundedBoxscoreLogsSource:
    """Playoff log fallback built from ESPN boxscores over a bounded window.

    Basketball-Reference can lag the postseason and the ESPN gamelog page is
    often behind a WAF challenge, which leaves a prop subject with a regular
    season log and no completed playoff game. This source fills that gap
    without opening an unbounded scan:

    * one scoreboard request per date in the window, cached per date and shared
      across every player in the run;
    * boxscores cached per event id and reused for both teams;
    * only games dated before the screen date, so the window stays
      point-in-time;
    * a missing boxscore or a DNP adds no row rather than a fabricated
      zero-minute game.
    """

    def __init__(
        self,
        cache: JsonCache,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        max_games: int = DEFAULT_MAX_GAMES,
    ) -> None:
        self.cache = cache
        self.lookback_days = max(lookback_days, 1)
        self.max_games = max(max_games, 1)
        self.slate_source = EspnSlateSource()
        self.boxscore_source = EspnBoxscoreSource(cache)
        self.window_start: date | None = None
        self.window_end: date | None = None
        self.dates_with_games = 0
        self.slate_failures = 0
        self._window_screen_date: date | None = None
        self._window: list[dict] = []

    def fetch_logs(self, player_name: str, team_abbr: str, screen_date: date) -> list[PlayerGameLog]:
        games = [
            game
            for game in self._window_games(screen_date)
            if team_abbr in {game["home_team"], game["away_team"]}
        ][: self.max_games]
        if not games:
            return []

        player_norms = _candidate_norms(player_name)
        logs: list[PlayerGameLog] = []
        for game in games:
            try:
                stat_lines = self.boxscore_source.fetch_boxscore(game["game_id"])
            except Exception:
                continue
            stat_line = None
            for player_norm in player_norms:
                stat_line = stat_lines.get((player_norm, team_abbr))
                if stat_line is not None:
                    break
            if stat_line is None or not _did_play(stat_line):
                continue

            opponent = game["away_team"] if game["home_team"] == team_abbr else game["home_team"]
            logs.append(
                PlayerGameLog(
                    player_name_raw=stat_line.player_name_raw,
                    player_name_norm=stat_line.player_name_norm,
                    game_date=date.fromisoformat(game["game_date"]),
                    team=team_abbr,
                    opponent=opponent,
                    minutes=stat_line.minutes,
                    points=stat_line.points,
                    rebounds=stat_line.rebounds,
                    assists=stat_line.assists,
                    threes_made=stat_line.threes_made,
                    did_play=True,
                    source="espn_boxscore_logs",
                )
            )
            if len(logs) >= self.max_games:
                break
        return logs

    def _window_games(self, screen_date: date) -> list[dict]:
        if self._window_screen_date == screen_date:
            return self._window

        window_end = screen_date - timedelta(days=1)
        window_start = window_end - timedelta(days=self.lookback_days - 1)
        self.window_start = window_start
        self.window_end = window_end

        games: list[dict] = []
        consecutive_failures = 0
        current = window_end
        while current >= window_start:
            day_games, failed = self._day_games(current)
            if failed:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_SLATE_FAILURES:
                    break
            else:
                consecutive_failures = 0
                games.extend(day_games)
            current -= timedelta(days=1)

        games.sort(key=lambda game: (game["game_date"], game["game_id"]), reverse=True)
        self.dates_with_games = len({game["game_date"] for game in games})
        self._window_screen_date = screen_date
        self._window = games
        return games

    def _day_games(self, game_date: date) -> tuple[list[dict], bool]:
        cache_key = f"espn_boxscore_window_day_{game_date.strftime('%Y%m%d')}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return list(cached), False
        try:
            games = self.slate_source.fetch_games(game_date)
        except Exception:
            self.slate_failures += 1
            return [], True
        payload = [
            {
                "game_id": game.game_id,
                "game_date": game.game_date.isoformat(),
                "home_team": game.home_team,
                "away_team": game.away_team,
            }
            for game in games
        ]
        self.cache.set(cache_key, payload)
        return payload, False
