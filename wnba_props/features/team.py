from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Iterable, Mapping, Optional, Sequence

from ..models import PlayerGameLog


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


def build_team_features(
    *,
    team: str,
    opponent: str,
    game_date: date,
    results: Sequence[TeamGameResult],
    is_home: Optional[bool] = None,
    star_available: bool = True,
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

    return TeamFeatures(
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
