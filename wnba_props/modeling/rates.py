from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..features.player import PlayerFeatures


LEAGUE_GAME_TOTAL_BASELINE = 164.0
MAX_ENVIRONMENT_ADJUSTMENT = 0.05


@dataclass(frozen=True)
class RateProjection:
    projected_rate: float
    base_rate: float
    opponent_factor: float
    recent_weight: float
    environment_factor: float = 1.0


def game_environment_factor(
    game_total: Optional[float],
    baseline: float = LEAGUE_GAME_TOTAL_BASELINE,
    max_adjustment: float = MAX_ENVIRONMENT_ADJUSTMENT,
) -> float:
    """Scale a per-minute rate by how far the expected game total sits from league norm.

    High-total (fast) games lift opportunity; low-total (slow) games cut it. The
    nudge is deliberately small and capped so a single environment signal cannot
    dominate the player's own rate.
    """
    if game_total is None or baseline <= 0.0:
        return 1.0
    raw_adjustment = 0.5 * ((game_total - baseline) / baseline)
    return 1.0 + max(-max_adjustment, min(max_adjustment, raw_adjustment))


def project_rate(
    features: PlayerFeatures,
    *,
    league_baseline: Optional[float] = None,
    opponent_weight: float = 0.5,
    positional_baseline: Optional[float] = None,
    positional_weight: float = 0.5,
    game_total: Optional[float] = None,
    game_total_baseline: float = LEAGUE_GAME_TOTAL_BASELINE,
) -> RateProjection:
    """Blend recency-weighted and season per-minute rates.

    When a league baseline for the stat's per-game allowance is supplied,
    the projected rate is nudged toward the opponent's allowed rate. When a
    positional baseline is also available the team-level and position-level
    allowances are blended. When an expected game total is supplied, the rate
    is scaled by the game environment factor.
    """
    recent_games = max(0, min(features.games_played, 10))
    recent_weight = recent_games / (recent_games + 8.0)
    base_rate = (
        recent_weight * features.rate_recency_weighted
        + (1.0 - recent_weight) * features.rate_season
    )

    opponent_factor = 1.0
    ratio: Optional[float] = None
    if (
        league_baseline is not None
        and league_baseline > 0.0
        and features.opponent_allowance is not None
    ):
        ratio = features.opponent_allowance / league_baseline
    if (
        positional_baseline is not None
        and positional_baseline > 0.0
        and features.opponent_positional_allowance is not None
    ):
        positional_ratio = features.opponent_positional_allowance / positional_baseline
        ratio = (
            positional_ratio
            if ratio is None
            else positional_weight * positional_ratio + (1.0 - positional_weight) * ratio
        )
    if ratio is not None:
        opponent_factor = 1.0 + opponent_weight * (ratio - 1.0)
        opponent_factor = max(0.80, min(1.20, opponent_factor))

    environment_factor = game_environment_factor(game_total, game_total_baseline)
    projected_rate = base_rate * opponent_factor * environment_factor
    return RateProjection(
        projected_rate=max(0.0, projected_rate),
        base_rate=base_rate,
        opponent_factor=opponent_factor,
        recent_weight=recent_weight,
        environment_factor=environment_factor,
    )
