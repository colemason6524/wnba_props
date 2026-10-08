from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Iterable, Mapping, Optional, Sequence

from ..config import TEAM_INJURY_REPLACEMENT_PPG, TEAM_INJURY_STARTER_MINUTES
from ..models import PlayerGameLog
from ..rotation import is_redistributable_out


@dataclass(frozen=True)
class TeamGameResult:
    game_date: date
    team: str
    opponent: str
    team_score: int
    opponent_score: int
    home: bool


@dataclass(frozen=True)
class TeamFeatures:
    team: str
    opponent: str
    game_date: date
    is_home: bool
    games_played: int
    ppg_last_5: float
    ppg_last_10: float
    ppg_season: float
    opp_ppg_allowed_last_5: float
    opp_ppg_allowed_last_10: float
    opp_ppg_allowed_season: float
    margin_last_5: float
    rest_days: int
    pace_proxy_last_5: float
    pace_proxy_season: float
    star_available: bool = True
    star_out: bool = False
    star_out_points: float = 0.0


def build_team_game_results(
    logs: Iterable[PlayerGameLog],
) -> list[TeamGameResult]:
    """Aggregate player logs into team-level game results.

    Requires near-complete rosters for both teams so that summed player
    points approximate the final team score on each side of the matchup.
    Games lacking an opposing side are dropped.
    """
    by_game: dict[tuple[date, str], dict[str, object]] = {}
    for log in logs:
        if not log.did_play or log.minutes <= 0.0:
            continue
        key = (log.game_date, log.team)
        entry = by_game.setdefault(
            key,
            {"opponent": log.opponent, "points": 0},
        )
        entry["points"] = int(entry["points"]) + int(log.points)

    results: list[TeamGameResult] = []
    seen: set[tuple[date, str, str]] = set()
    for (game_date, team), entry in by_game.items():
        opponent = str(entry["opponent"])
        opp_entry = by_game.get((game_date, opponent))
        if opp_entry is None:
            continue
        marker = (game_date, team, opponent)
        if marker in seen:
            continue
        seen.add(marker)
        results.append(
            TeamGameResult(
                game_date=game_date,
                team=team,
                opponent=opponent,
                team_score=int(entry["points"]),
                opponent_score=int(opp_entry["points"]),
                home=False,
            )
        )
    results.sort(key=lambda item: (item.game_date, item.team))
    return results


def _rolling_average(values: Sequence[float], window: int) -> float:
    if not values:
        return 0.0
    subset = values[:window]
    return mean(subset)


def _resolve_home(
    team: str,
    opponent: str,
    game_date: date,
    home_map: Optional[Mapping[tuple[date, str], bool]],
) -> bool:
    if home_map is None:
        return False
    return bool(home_map.get((game_date, team), False))


def _starter_base_minutes(
    logs: Sequence[PlayerGameLog],
    *,
    window: int = 10,
) -> float:
    """Recency-weighted base minutes, mirroring board._base_minutes_for_player."""
    eligible = [log for log in logs if log.did_play and log.minutes > 0.0]
    if not eligible:
        return 0.0
    eligible.sort(key=lambda log: log.game_date, reverse=True)
    recent = eligible[:window]
    weights = [0.5 ** (index / 6.0) for index in range(len(recent))]
    recency = sum(log.minutes * w for log, w in zip(recent, weights)) / sum(weights)
    season = sum(log.minutes for log in eligible) / len(eligible)
    return 0.65 * recency + 0.35 * season


def _season_ppg(logs: Sequence[PlayerGameLog]) -> float:
    """Mean points per game over games played."""
    eligible = [log for log in logs if log.did_play]
    if not eligible:
        return 0.0
    return sum(float(log.points) for log in eligible) / len(eligible)


def out_starter_points_above_replacement(
    *,
    team: str,
    player_statuses: Optional[Mapping[str, str]] = None,
    logs_by_player: Optional[Mapping[str, Sequence[PlayerGameLog]]] = None,
    replacement_ppg: float = TEAM_INJURY_REPLACEMENT_PPG,
    starter_minutes: float = TEAM_INJURY_STARTER_MINUTES,
) -> float:
    """Points above replacement lost to OUT starters on ``team``.

    Only players whose status vacates minutes (out / injured reserve /
    suspended -- see rotation.is_redistributable_out) with starter-level
    base minutes count. Each counts ``max(0, season_ppg - replacement_ppg)``.
    Day-to-day and other uncertain statuses never count.
    """
    lost = 0.0
    for norm, status in (player_statuses or {}).items():
        if not is_redistributable_out(status):
            continue
        logs = (logs_by_player or {}).get(norm, [])
        if not logs:
            continue
        latest = max(logs, key=lambda log: log.game_date)
        if latest.team != team:
            continue
        if _starter_base_minutes(logs) < starter_minutes:
            continue
        lost += max(0.0, _season_ppg(logs) - replacement_ppg)
    return lost


def build_team_features(
    *,
    team: str,
    opponent: str,
    game_date: date,
    results: Sequence[TeamGameResult],
    is_home: Optional[bool] = None,
    star_available: bool = True,
    player_statuses: Optional[Mapping[str, str]] = None,
    logs_by_player: Optional[Mapping[str, Sequence[PlayerGameLog]]] = None,
    replacement_ppg: float = TEAM_INJURY_REPLACEMENT_PPG,
) -> Optional[TeamFeatures]:
    """Point-in-time team features for a single scheduled game.

    Only games strictly before ``game_date`` are used, so the feature
    vector is safe to feed into a walk-forward fit.
    """
    prior = sorted(
        [r for r in results if r.team == team and r.game_date < game_date],
        key=lambda item: item.game_date,
        reverse=True,
    )
    if not prior:
        return None

    opp_prior = sorted(
        [r for r in results if r.team == opponent and r.game_date < game_date],
        key=lambda item: item.game_date,
        reverse=True,
    )

    points_for = [float(r.team_score) for r in prior]
    points_against = [float(r.opponent_score) for r in prior]
    margins = [
        float(r.team_score - r.opponent_score) for r in prior
    ]

    opp_points_allowed = [float(r.opponent_score) for r in opp_prior]
    if not opp_points_allowed:
        opp_points_allowed = [float(r.team_score) for r in prior]

    combined_ppr = [
        float(r.team_score + r.opponent_score) for r in prior
    ]

    resolved_home = (
        is_home
        if is_home is not None
        else _resolve_home(team, opponent, game_date, None)
    )

    base = TeamFeatures(
        team=team,
        opponent=opponent,
        game_date=game_date,
        is_home=bool(resolved_home),
        games_played=len(prior),
        ppg_last_5=_rolling_average(points_for, 5),
        ppg_last_10=_rolling_average(points_for, 10),
        ppg_season=mean(points_for),
        opp_ppg_allowed_last_5=_rolling_average(opp_points_allowed, 5),
        opp_ppg_allowed_last_10=_rolling_average(opp_points_allowed, 10),
        opp_ppg_allowed_season=mean(opp_points_allowed),
        margin_last_5=_rolling_average(margins, 5),
        rest_days=_rest_days(prior[0].game_date, game_date),
        pace_proxy_last_5=_rolling_average(combined_ppr, 5),
        pace_proxy_season=mean(combined_ppr),
        star_available=star_available,
    )

    lost = 0.0
    if player_statuses and logs_by_player:
        lost = out_starter_points_above_replacement(
            team=team,
            player_statuses=player_statuses,
            logs_by_player=logs_by_player,
            replacement_ppg=replacement_ppg,
        )
    if lost > 0.0:
        print("[team-injury] team=%s debit=%.1fpts OUT starter(s) above replacement" % (team, lost))
        return dataclasses.replace(
            base,
            ppg_last_5=base.ppg_last_5 - lost,
            ppg_last_10=base.ppg_last_10 - lost,
            ppg_season=base.ppg_season - lost,
            margin_last_5=base.margin_last_5 - lost,
            star_out=True,
            star_out_points=lost,
        )
    return base


def _rest_days(last_game: date, game_date: date) -> int:
    delta = (game_date - last_game).days
    return max(0, delta)


def parse_team_results_from_scoreboard(
    payload: dict,
    *,
    game_date: date,
    team_map: Mapping[str, str],
) -> list[TeamGameResult]:
    """Parse final team scores from an ESPN scoreboard payload."""
    results: list[TeamGameResult] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        competition = competitions[0]
        status = ((event.get("status") or {}).get("type") or {})
        if not status.get("completed", False):
            continue
        home_team = away_team = ""
        home_score = away_score = None
        for competitor in competition.get("competitors", []):
            team_payload = competitor.get("team", {})
            display = team_payload.get("displayName", "")
            abbr = team_map.get(display, team_payload.get("abbreviation", ""))
            score = _int_or_none(competitor.get("score"))
            if competitor.get("homeAway") == "home":
                home_team = abbr
                home_score = score
            else:
                away_team = abbr
                away_score = score
        if not home_team or not away_team or home_score is None or away_score is None:
            continue
        results.append(
            TeamGameResult(
                game_date=game_date,
                team=home_team,
                opponent=away_team,
                team_score=home_score,
                opponent_score=away_score,
                home=True,
            )
        )
        results.append(
            TeamGameResult(
                game_date=game_date,
                team=away_team,
                opponent=home_team,
                team_score=away_score,
                opponent_score=home_score,
                home=False,
            )
        )
    return results


def _int_or_none(value: object) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
