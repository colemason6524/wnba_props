from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..features.player import PlayerFeatures
from .calibration import ResidualArtifact, calibrate_probability


@dataclass(frozen=True)
class MinutesProjection:
    projected_minutes: float
    minutes_sd: float
    starter_probability: float
    availability_uncertain: bool
    availability_excluded: bool = False


@dataclass(frozen=True)
class SimulationResult:
    over_probability: float
    under_probability: float
    push_probability: float
    conditional_over_probability: float
    conditional_under_probability: float
    raw_conditional_over_probability: float
    projected_mean: float
    percentile_10: float
    percentile_90: float
    samples: int


_EXCLUDED_STATUSES = {"out", "injured reserve", "suspended"}
_UNCERTAIN_STATUSES = {
    "day-to-day",
    "questionable",
    "doubtful",
    "probable",
    "game-time decision",
}


def project_minutes(
    features: PlayerFeatures,
    *,
    player_status: str = "",
    recent_weight: float = 0.65,
) -> MinutesProjection:
    status = player_status.strip().lower()
    excluded = status in _EXCLUDED_STATUSES
    uncertain = status in _UNCERTAIN_STATUSES

    projected = (
        recent_weight * features.minutes_recency_weighted
        + (1.0 - recent_weight) * features.minutes_avg_season
    )

    minutes_sd = _clamp(
        (0.70 * features.minutes_sd_l10) + (0.30 * 3.5),
        2.0,
        6.0,
    )
    if features.games_last_7_days >= 4:
        minutes_sd = min(6.5, minutes_sd + 0.5)
    if uncertain:
        minutes_sd = min(7.0, minutes_sd + 1.0)

    starter_probability = _clamp(features.minutes_avg_l5 / 30.0, 0.0, 1.0)
    if features.starting:
        starter_probability = max(starter_probability, 0.6)

    return MinutesProjection(
        projected_minutes=_clamp(projected, 0.0, 40.0),
        minutes_sd=minutes_sd,
        starter_probability=starter_probability,
        availability_uncertain=uncertain,
        availability_excluded=excluded,
    )


def simulate_prop(
    *,
    features: PlayerFeatures,
    minutes: MinutesProjection,
    projected_rate: float,
    line: float,
    residuals: ResidualArtifact,
    simulations: int = 10_000,
    seed_material: str = "",
) -> SimulationResult:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    if not residuals.pairs:
        raise ValueError("residual pairs must not be empty")

    seed = _seed(features, line, residuals, seed_material)
    rng = random.Random(seed)
    pair_count = len(residuals.pairs)

    wins_over = 0
    wins_under = 0
    pushes = 0
    total = 0.0
    values: list[int] = []

    for _ in range(simulations):
        minutes_z, rate_error = residuals.pairs[rng.randrange(pair_count)]
        sampled_minutes = max(0.0, minutes.projected_minutes + minutes.minutes_sd * minutes_z)
        sampled_rate = max(0.0, projected_rate + rate_error)
        value = max(0, int(round(sampled_minutes * sampled_rate)))
        values.append(value)
        total += value
        if value > line:
            wins_over += 1
        elif value < line:
            wins_under += 1
        else:
            pushes += 1

    non_push = wins_over + wins_under
    raw_conditional_over = wins_over / non_push if non_push else 0.5
    conditional_over = calibrate_probability(
        raw_conditional_over, residuals.calibration_lambda
    )
    conditional_under = 1.0 - conditional_over

    stay_probability = 1.0 - (pushes / simulations)
    over_probability = stay_probability * conditional_over
    under_probability = stay_probability * conditional_under
    push_probability = pushes / simulations

    values.sort()
    return SimulationResult(
        over_probability=over_probability,
        under_probability=under_probability,
        push_probability=push_probability,
        conditional_over_probability=conditional_over,
        conditional_under_probability=conditional_under,
        raw_conditional_over_probability=raw_conditional_over,
        projected_mean=total / simulations,
        percentile_10=float(_percentile(values, 0.10)),
        percentile_90=float(_percentile(values, 0.90)),
        samples=simulations,
    )


def _percentile(sorted_values: Sequence[int], probability: float) -> int:
    index = round((len(sorted_values) - 1) * probability)
    return int(sorted_values[index])


def _seed(
    features: PlayerFeatures,
    line: float,
    residuals: ResidualArtifact,
    seed_material: str,
) -> int:
    raw = "|".join(
        [
            residuals.model_id,
            residuals.sha256,
            features.player_name_norm,
            features.prop_type,
            features.game_date.isoformat(),
            str(line),
            seed_material,
        ]
    )
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
