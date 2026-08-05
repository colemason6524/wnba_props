from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import mean, median, pstdev

from ..models import PlayerGameLog, PropLine
from .models import MODEL_VERSION, ShadowProjection
from .pricing import expected_profit_units, fair_american_odds, implied_probability


@dataclass(frozen=True)
class ProjectionConfig:
    simulations: int = 10_000
    minimum_games: int = 5
    recent_games: int = 10
    recency_half_life_games: float = 6.0
    league_game_total_baseline: float = 164.0


def project_points_line(
    *,
    line: PropLine,
    game_time: datetime,
    logs: list[PlayerGameLog],
    screen_date: date,
    team_spread: float | None = None,
    game_total: float | None = None,
    player_status: str = "",
    config: ProjectionConfig | None = None,
) -> ShadowProjection | None:
    config = config or ProjectionConfig()
    if line.prop_type != "PTS":
        return None

    eligible_logs = sorted(
        [
            log
            for log in logs
            if log.did_play and log.minutes > 0.0 and log.game_date < screen_date
        ],
        key=lambda log: log.game_date,
        reverse=True,
    )
    if len(eligible_logs) < config.minimum_games:
        return None

    status = player_status.strip().lower()
    if status in {"out", "injured reserve", "suspended"}:
        return None

    recent_logs = eligible_logs[: config.recent_games]
    season_minutes_avg = mean(log.minutes for log in eligible_logs)
    recent_minutes_avg = _weighted_mean(
        [log.minutes for log in recent_logs],
        config.recency_half_life_games,
    )
    projected_minutes = (0.65 * recent_minutes_avg) + (0.35 * season_minutes_avg)

    flags: list[str] = []
    if team_spread is None:
        flags.append("SPREAD_MISSING")
    if game_total is None:
        flags.append("GAME_TOTAL_MISSING")
    if team_spread is not None and abs(team_spread) >= 10.0 and projected_minutes >= 28.0:
        blowout_reduction = min(1.5, max(0.0, abs(team_spread) - 8.0) * 0.2)
        projected_minutes -= blowout_reduction
        flags.append("BLOWOUT_MINUTES")

    recent_minutes = [log.minutes for log in recent_logs]
    observed_minutes_sd = pstdev(recent_minutes) if len(recent_minutes) >= 2 else 3.5
    minutes_sd = _clamp((0.70 * observed_minutes_sd) + (0.30 * 3.5), 2.0, 6.0)
    if status in {"day-to-day", "questionable", "doubtful", "probable", "game-time decision"}:
        minutes_sd = min(7.0, minutes_sd + 1.0)
        flags.append("AVAILABILITY_UNCERTAIN")
    projected_minutes = _clamp(projected_minutes, 0.0, 40.0)

    season_points_per_minute = sum(log.points for log in eligible_logs) / sum(
        log.minutes for log in eligible_logs
    )
    recent_points_per_minute = _weighted_rate(
        recent_logs,
        config.recency_half_life_games,
    )
    recent_weight = len(recent_logs) / (len(recent_logs) + 8.0)
    projected_points_per_minute = (
        recent_weight * recent_points_per_minute
        + (1.0 - recent_weight) * season_points_per_minute
    )

    environment_factor = _game_environment_factor(
        game_total,
        config.league_game_total_baseline,
    )
    projected_points_per_minute *= environment_factor

    rate_observations = [
        log.points / log.minutes
        for log in recent_logs
        if log.minutes >= 10.0
    ]
    observed_rate_sd = pstdev(rate_observations) if len(rate_observations) >= 2 else 0.15
    points_rate_sd = _clamp((0.75 * observed_rate_sd) + (0.25 * 0.15), 0.08, 0.35)

    seed = _projection_seed(line, screen_date)
    simulated_points = _simulate_points(
        seed=seed,
        simulations=config.simulations,
        projected_minutes=projected_minutes,
        minutes_sd=minutes_sd,
        projected_points_per_minute=projected_points_per_minute,
        points_rate_sd=points_rate_sd,
    )
    wins_over = sum(1 for value in simulated_points if value > line.line)
    wins_under = sum(1 for value in simulated_points if value < line.line)
    pushes = len(simulated_points) - wins_over - wins_under
    over_probability = wins_over / len(simulated_points)
    under_probability = wins_under / len(simulated_points)
    push_probability = pushes / len(simulated_points)

    non_push_probability = over_probability + under_probability
    conditional_over = over_probability / non_push_probability if non_push_probability else 0.5
    conditional_under = under_probability / non_push_probability if non_push_probability else 0.5
    over_ev = expected_profit_units(over_probability, under_probability, line.over_odds)
    under_ev = expected_profit_units(under_probability, over_probability, line.under_odds)

    price_status = _price_status(line)
    model_side = _model_side(
        over_probability=conditional_over,
        under_probability=conditional_under,
        over_expected_value=over_ev,
        under_expected_value=under_ev,
        price_status=price_status,
    )
    if price_status != "BOTH_SIDES_PRICED":
        flags.append("PRICE_INCOMPLETE")

    sorted_points = sorted(simulated_points)
    return ShadowProjection(
        model_version=MODEL_VERSION,
        created_at=datetime.now(timezone.utc),
        screen_date=screen_date,
        game_id=line.event_id,
        game_time=game_time,
        player_name=line.player_name_raw,
        player_name_norm=line.player_name_norm,
        team=line.team,
        opponent=line.opponent,
        prop_type=line.prop_type,
        line=line.line,
        bookmaker=line.bookmaker,
        line_collected_at=line.collected_at,
        over_odds=line.over_odds,
        under_odds=line.under_odds,
        games_used=len(eligible_logs),
        last_game_date=eligible_logs[0].game_date,
        projected_minutes=round(projected_minutes, 2),
        minutes_sd=round(minutes_sd, 2),
        season_minutes_avg=round(season_minutes_avg, 2),
        recent_minutes_avg=round(recent_minutes_avg, 2),
        season_points_per_minute=round(season_points_per_minute, 4),
        recent_points_per_minute=round(recent_points_per_minute, 4),
        projected_points_per_minute=round(projected_points_per_minute, 4),
        team_spread=team_spread,
        game_total=game_total,
        game_environment_factor=round(environment_factor, 4),
        projected_mean=round(mean(simulated_points), 2),
        projected_median=round(float(median(simulated_points)), 2),
        percentile_10=float(_percentile(sorted_points, 0.10)),
        percentile_90=float(_percentile(sorted_points, 0.90)),
        over_probability=round(over_probability, 4),
        under_probability=round(under_probability, 4),
        push_probability=round(push_probability, 4),
        fair_over_odds=fair_american_odds(conditional_over),
        fair_under_odds=fair_american_odds(conditional_under),
        over_break_even_probability=_rounded_optional(implied_probability(line.over_odds)),
        under_break_even_probability=_rounded_optional(implied_probability(line.under_odds)),
        over_expected_value=_rounded_optional(over_ev),
        under_expected_value=_rounded_optional(under_ev),
        model_side=model_side,
        price_status=price_status,
        flags=flags,
    )


def _weighted_mean(values: list[float], half_life_games: float) -> float:
    weights = [math.pow(0.5, index / half_life_games) for index in range(len(values))]
    return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)


def _weighted_rate(logs: list[PlayerGameLog], half_life_games: float) -> float:
    weights = [math.pow(0.5, index / half_life_games) for index in range(len(logs))]
    weighted_points = sum(log.points * weight for log, weight in zip(logs, weights))
    weighted_minutes = sum(log.minutes * weight for log, weight in zip(logs, weights))
    return weighted_points / weighted_minutes


def _game_environment_factor(game_total: float | None, baseline: float) -> float:
    if game_total is None or baseline <= 0.0:
        return 1.0
    raw_adjustment = 0.5 * ((game_total - baseline) / baseline)
    return 1.0 + _clamp(raw_adjustment, -0.05, 0.05)


def _simulate_points(
    *,
    seed: int,
    simulations: int,
    projected_minutes: float,
    minutes_sd: float,
    projected_points_per_minute: float,
    points_rate_sd: float,
) -> list[int]:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    rng = random.Random(seed)
    results: list[int] = []
    for _ in range(simulations):
        minutes = _clamp(rng.gauss(projected_minutes, minutes_sd), 0.0, 40.0)
        points_rate = max(0.0, rng.gauss(projected_points_per_minute, points_rate_sd))
        results.append(max(0, int(round(minutes * points_rate))))
    return results


def _projection_seed(line: PropLine, screen_date: date) -> int:
    raw = "|".join(
        [
            MODEL_VERSION,
            screen_date.isoformat(),
            line.event_id,
            line.player_name_norm,
            line.prop_type,
            str(line.line),
        ]
    )
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)


def _price_status(line: PropLine) -> str:
    if line.over_odds is not None and line.under_odds is not None:
        return "BOTH_SIDES_PRICED"
    if line.over_odds is not None or line.under_odds is not None:
        return "ONE_SIDE_PRICED"
    return "NO_PRICE"


def _model_side(
    *,
    over_probability: float,
    under_probability: float,
    over_expected_value: float | None,
    under_expected_value: float | None,
    price_status: str,
) -> str:
    if price_status == "BOTH_SIDES_PRICED":
        candidates = [
            (over_expected_value if over_expected_value is not None else float("-inf"), "OVER"),
            (under_expected_value if under_expected_value is not None else float("-inf"), "UNDER"),
        ]
        best_ev, best_side = max(candidates)
        return best_side if best_ev > 0.0 else "PASS"
    if over_probability >= 0.55:
        return "OVER"
    if under_probability >= 0.55:
        return "UNDER"
    return "PASS"


def _percentile(sorted_values: list[int], probability: float) -> int:
    index = round((len(sorted_values) - 1) * probability)
    return sorted_values[index]


def _rounded_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
