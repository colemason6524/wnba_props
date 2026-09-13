from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Callable, Iterable, Mapping, Optional, Sequence

from ..models import PlayerGameLog


_STAT_ACCESSORS: dict[str, Callable[[PlayerGameLog], int]] = {
    "PTS": lambda log: log.points,
    "REB": lambda log: log.rebounds,
    "AST": lambda log: log.assists,
    "3PM": lambda log: log.threes_made,
}


def stat_value(log: PlayerGameLog, prop_type: str) -> int:
    accessor = _STAT_ACCESSORS.get(prop_type.upper())
    if accessor is None:
        raise ValueError(f"unsupported prop type: {prop_type}")
    return accessor(log)


@dataclass(frozen=True)
class PlayerFeatures:
    player_name_raw: str
    player_name_norm: str
    team: str
    opponent: str
    game_date: date
    prop_type: str
    games_played: int
    minutes_avg_l5: float
    minutes_avg_l10: float
    minutes_avg_season: float
    minutes_recency_weighted: float
    minutes_sd_l10: float
    rate_l5: float
    rate_l10: float
    rate_season: float
    rate_recency_weighted: float
    rate_sd: float
    trend: float
    games_last_7_days: int
    games_last_14_days: int
    opponent_allowance: Optional[float]
    starting: bool


def _weighted_mean(values: Sequence[float], half_life_games: float) -> float:
    if not values:
        return 0.0
    weights = [
        math.pow(0.5, index / half_life_games) for index in range(len(values))
    ]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_rate(logs: Sequence[PlayerGameLog], prop_type: str, half_life: float) -> float:
    if not logs:
        return 0.0
    weights = [
        math.pow(0.5, index / half_life) for index in range(len(logs))
    ]
    weighted_stat = sum(stat_value(log, prop_type) * w for log, w in zip(logs, weights))
    weighted_minutes = sum(log.minutes * w for log, w in zip(logs, weights))
    return weighted_stat / weighted_minutes if weighted_minutes > 0.0 else 0.0


def _rate(logs: Sequence[PlayerGameLog], prop_type: str) -> float:
    total_minutes = sum(log.minutes for log in logs)
    if total_minutes <= 0.0:
        return 0.0
    total_stat = sum(stat_value(log, prop_type) for log in logs)
    return total_stat / total_minutes


def _opponent_allowance(
    *,
    logs: Sequence[PlayerGameLog],
    opponent: str,
    prop_type: str,
    game_date: date,
) -> Optional[float]:
    """Average per-game total the opponent allows for the given stat.

    Uses every available player-game where the opponent was the opposing
    team, grouped by game date so that partial rosters still produce a
    per-game team allowance.
    """
    by_game: dict[date, int] = {}
    for log in logs:
        if log.opponent != opponent:
            continue
        if log.game_date >= game_date:
            continue
        if not log.did_play or log.minutes <= 0.0:
            continue
        by_game[log.game_date] = by_game.get(log.game_date, 0) + stat_value(log, prop_type)
    if not by_game:
        return None
    return mean(by_game.values())


def build_player_features(
    *,
    logs: Sequence[PlayerGameLog],
    league_logs: Sequence[PlayerGameLog],
    player_name_norm: str,
    player_name_raw: str,
    team: str,
    opponent: str,
    game_date: date,
    prop_type: str,
    minimum_games: int = 5,
    recent_games: int = 10,
    recency_half_life_games: float = 6.0,
    opponent_allowance_override: Optional[float] = None,
) -> Optional[PlayerFeatures]:
    """Point-in-time player features for a single prop line."""
    prop_type = prop_type.upper()
    if prop_type not in _STAT_ACCESSORS:
        raise ValueError(f"unsupported prop type: {prop_type}")

    eligible = sorted(
        [
            log
            for log in logs
            if log.player_name_norm == player_name_norm
            and log.did_play
            and log.minutes > 0.0
            and log.game_date < game_date
        ],
        key=lambda log: log.game_date,
        reverse=True,
    )
    if len(eligible) < minimum_games:
        return None

    recent = eligible[:recent_games]
    minutes_series = [log.minutes for log in recent]

    minutes_avg_l5 = mean([log.minutes for log in eligible[:5]])
    minutes_avg_l10 = mean([log.minutes for log in eligible[:10]])
    minutes_avg_season = mean(log.minutes for log in eligible)
    minutes_recency = _weighted_mean(minutes_series, recency_half_life_games)
    minutes_sd = pstdev(minutes_series) if len(minutes_series) >= 2 else 3.5

    rate_l5 = _rate(eligible[:5], prop_type)
    rate_l10 = _rate(eligible[:10], prop_type)
    rate_season = _rate(eligible, prop_type)
    rate_recency = _weighted_rate(recent, prop_type, recency_half_life_games)

    rate_obs = [
        stat_value(log, prop_type) / log.minutes
        for log in recent
        if log.minutes >= 10.0
    ]
    rate_sd = pstdev(rate_obs) if len(rate_obs) >= 2 else 0.0

    games_last_7 = sum(
        1 for log in eligible if (game_date - log.game_date).days <= 7
    )
    games_last_14 = sum(
        1 for log in eligible if (game_date - log.game_date).days <= 14
    )

    opponent_allowance = (
        opponent_allowance_override
        if opponent_allowance_override is not None
        else _opponent_allowance(
            logs=league_logs,
            opponent=opponent,
            prop_type=prop_type,
            game_date=game_date,
        )
    )

    starting = minutes_avg_l5 >= 24.0

    return PlayerFeatures(
        player_name_raw=player_name_raw,
        player_name_norm=player_name_norm,
        team=team,
        opponent=opponent,
        game_date=game_date,
        prop_type=prop_type,
        games_played=len(eligible),
        minutes_avg_l5=minutes_avg_l5,
        minutes_avg_l10=minutes_avg_l10,
        minutes_avg_season=minutes_avg_season,
        minutes_recency_weighted=minutes_recency,
        minutes_sd_l10=minutes_sd,
        rate_l5=rate_l5,
        rate_l10=rate_l10,
        rate_season=rate_season,
        rate_recency_weighted=rate_recency,
        rate_sd=rate_sd,
        trend=rate_l5 - rate_season,
        games_last_7_days=games_last_7,
        games_last_14_days=games_last_14,
        opponent_allowance=opponent_allowance,
        starting=starting,
    )


def league_stat_baselines(
    logs: Iterable[PlayerGameLog],
    prop_types: Sequence[str],
) -> dict[str, float]:
    """Average per-game team total for each stat across the league.

    A team-game is one (team, game_date) pair. Dividing the summed stat by the
    number of team-games yields the same per-game scale that
    ``_opponent_allowance`` produces, so the ratio is a defensive multiplier.
    """
    totals: dict[str, float] = {}
    team_games: dict[str, set[tuple[str, date]]] = {}
    for log in logs:
        if not log.did_play or log.minutes <= 0.0:
            continue
        marker = (log.team, log.game_date)
        for prop_type in prop_types:
            key = prop_type.upper()
            totals[key] = totals.get(key, 0.0) + stat_value(log, prop_type)
            team_games.setdefault(key, set()).add(marker)
    baselines: dict[str, float] = {}
    for key, total in totals.items():
        games = len(team_games.get(key, ()))
        if games > 0:
            baselines[key] = total / games
    return baselines


def _allowance_lookup(
    logs: Sequence[PlayerGameLog],
    prop_types: Sequence[str],
) -> dict[tuple[str, str], tuple[list[date], list[float]]]:
    """Precompute cumulative per-game stat totals allowed by each opponent.

    Returns a mapping of (opponent, prop_type) to sorted game dates and the
    prefix sums of per-game allowed totals so point-in-time averages can be
    resolved in logarithmic time.
    """
    totals: dict[tuple[str, str], dict[date, int]] = {}
    for log in logs:
        if not log.did_play or log.minutes <= 0.0:
            continue
        for prop_type in prop_types:
            key = (log.opponent, prop_type.upper())
            bucket = totals.setdefault(key, {})
            bucket[log.game_date] = bucket.get(log.game_date, 0) + stat_value(log, prop_type)

    lookup: dict[tuple[str, str], tuple[list[date], list[float]]] = {}
    for key, bucket in totals.items():
        dates = sorted(bucket)
        prefix = [0.0]
        for day in dates:
            prefix.append(prefix[-1] + bucket[day])
        lookup[key] = (dates, prefix)
    return lookup


def _allowance_before(
    lookup: Mapping[tuple[str, str], tuple[list[date], list[float]]],
    opponent: str,
    prop_type: str,
    game_date: date,
) -> Optional[float]:
    entry = lookup.get((opponent, prop_type.upper()))
    if entry is None:
        return None
    dates, prefix = entry
    index = bisect.bisect_left(dates, game_date)
    if index == 0:
        return None
    total = prefix[index]
    return total / index


def build_player_feature_table(
    *,
    logs: Iterable[PlayerGameLog],
    prop_types: Sequence[str],
    minimum_games: int = 5,
    recent_games: int = 10,
    recency_half_life_games: float = 6.0,
) -> list[PlayerFeatures]:
    """Build a point-in-time feature row for every player-game in a log set.

    Each row only sees earlier games for the same player, which makes the
    table directly usable for walk-forward fitting.
    """
    log_list = list(logs)
    by_player: dict[str, list[PlayerGameLog]] = {}
    for log in log_list:
        by_player.setdefault(log.player_name_norm, []).append(log)

    allowance = _allowance_lookup(log_list, prop_types)

    rows: list[PlayerFeatures] = []
    for player_logs in by_player.values():
        for log in player_logs:
            if not log.did_play or log.minutes <= 0.0:
                continue
            for prop_type in prop_types:
                features = build_player_features(
                    logs=player_logs,
                    league_logs=log_list,
                    player_name_norm=log.player_name_norm,
                    player_name_raw=log.player_name_raw,
                    team=log.team,
                    opponent=log.opponent,
                    game_date=log.game_date,
                    prop_type=prop_type,
                    minimum_games=minimum_games,
                    recent_games=recent_games,
                    recency_half_life_games=recency_half_life_games,
                    opponent_allowance_override=_allowance_before(
                        allowance, log.opponent, prop_type, log.game_date
                    ),
                )
                if features is not None:
                    rows.append(features)
    rows.sort(key=lambda row: (row.game_date, row.player_name_norm, row.prop_type))
    return rows
