from __future__ import annotations

from datetime import date, timedelta

from ..cache import JsonCache
from ..models import PlayerGameLog
from ..utils import normalize_name
from .espn import EspnSlateSource
from .espn_boxscore import EspnBoxscoreSource


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
