from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..features.player import PlayerFeatures


@dataclass(frozen=True)
class RateProjection:
    projected_rate: float
    base_rate: float
    opponent_factor: float
    recent_weight: float


def project_rate(
    features: PlayerFeatures,
    *,
    league_baseline: Optional[float] = None,
    opponent_weight: float = 0.5,
) -> RateProjection:
    """Blend recency-weighted and season per-minute rates.

    When a league baseline for the stat's per-game allowance is supplied,
    the projected rate is nudged toward the opponent's allowed rate.
    """
    recent_games = max(0, min(features.games_played, 10))
    recent_weight = recent_games / (recent_games + 8.0)
    base_rate = (
        recent_weight * features.rate_recency_weighted
        + (1.0 - recent_weight) * features.rate_season
    )

    opponent_factor = 1.0
    if (
        league_baseline is not None
        and league_baseline > 0.0
        and features.opponent_allowance is not None
    ):
        raw_ratio = features.opponent_allowance / league_baseline
        opponent_factor = 1.0 + opponent_weight * (raw_ratio - 1.0)
        opponent_factor = max(0.80, min(1.20, opponent_factor))

    projected_rate = base_rate * opponent_factor
    return RateProjection(
        projected_rate=max(0.0, projected_rate),
        base_rate=base_rate,
        opponent_factor=opponent_factor,
        recent_weight=recent_weight,
    )
