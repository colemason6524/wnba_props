from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from ..features.player import PlayerFeatures
from ..models import PropLine
from .calibration import ResidualArtifact
from .minutes import MinutesProjection, project_minutes, simulate_prop
from .rates import LEAGUE_GAME_TOTAL_BASELINE, project_rate
from .value import (
    blend_non_push,
    expected_value_with_push,
    no_vig_probabilities,
    value_label,
)


@dataclass(frozen=True)
class PropForecast:
    player_name_raw: str
    player_name_norm: str
    team: str
    opponent: str
    game_date: date
    prop_type: str
    line: float
    bookmaker: str
    pick_side: str
    pick_probability: float
    over_probability: float
    under_probability: float
    push_probability: float
    projected_mean: float
    projected_minutes: float
    projected_rate: float
    percentile_10: float
    percentile_90: float
    price: Optional[int]
    ev: Optional[float]
    value_label: str
    over_odds: Optional[int]
    under_odds: Optional[int]
    flags: Sequence[str] = field(default_factory=list)
    market_source: str = ""
    captured_at: str = ""
    dnp_probability: float = 0.0
    void_probability: float = 0.0
    market_over_probability: Optional[float] = None
    over_ev: Optional[float] = None
    under_ev: Optional[float] = None
    market_blend_weight: float = 0.0


def forecast_prop(
    *,
    features: PlayerFeatures,
    line: PropLine,
    residuals: ResidualArtifact,
    player_status: str = "",
    league_baseline: Optional[float] = None,
    positional_baseline: Optional[float] = None,
    game_total: Optional[float] = None,
    game_total_baseline: float = LEAGUE_GAME_TOTAL_BASELINE,
    simulations: int = 10_000,
    market_weight: float = 0.0,
    ev_selection: bool = False,
) -> Optional[PropForecast]:
    minutes = project_minutes(features, player_status=player_status)
    if minutes.availability_excluded:
        return None

    rate = project_rate(
        features,
        league_baseline=league_baseline,
        positional_baseline=positional_baseline,
        game_total=game_total,
        game_total_baseline=game_total_baseline,
    )
    simulation = simulate_prop(
        features=features,
        minutes=minutes,
        projected_rate=rate.projected_rate,
        line=line.line,
        residuals=residuals,
        simulations=simulations,
        seed_material=line.bookmaker,
    )

    over_probability = simulation.over_probability
    under_probability = simulation.under_probability
    market_over_probability = None
    if market_weight > 0.0:
        market_over, market_under = no_vig_probabilities(
            line.over_odds, line.under_odds
        )
        over_probability, under_probability = blend_non_push(
            over_probability,
            under_probability,
            market_over,
            market_under,
            market_weight,
        )
        market_over_probability = market_over

    over_ev = expected_value_with_push(
        over_probability, under_probability, line.over_odds
    )
    under_ev = expected_value_with_push(
        under_probability, over_probability, line.under_odds
    )

    pick_side, pick_probability = _pick_side(
        over_probability,
        under_probability,
        simulation.push_probability,
        over_ev,
        under_ev,
        ev_selection,
    )
    if pick_side is None:
        return None

    price = line.over_odds if pick_side == "OVER" else line.under_odds
    ev = over_ev if pick_side == "OVER" else under_ev

    flags = _flags(features, minutes, pick_side)

    return PropForecast(
        player_name_raw=features.player_name_raw,
        player_name_norm=features.player_name_norm,
        team=features.team,
        opponent=features.opponent,
        game_date=features.game_date,
        prop_type=features.prop_type,
        line=line.line,
        bookmaker=line.bookmaker,
        pick_side=pick_side,
        pick_probability=pick_probability,
        over_probability=over_probability,
        under_probability=under_probability,
        push_probability=simulation.push_probability,
        projected_mean=simulation.projected_mean,
        projected_minutes=minutes.projected_minutes,
        projected_rate=rate.projected_rate,
        percentile_10=simulation.percentile_10,
        percentile_90=simulation.percentile_90,
        price=price,
        ev=ev,
        value_label=value_label(ev),
        over_odds=line.over_odds,
        under_odds=line.under_odds,
        flags=flags,
        market_source=line.bookmaker,
        captured_at=line.collected_at.isoformat(),
        dnp_probability=round(minutes.dnp_probability, 4),
        void_probability=round(simulation.void_probability, 4),
        market_over_probability=market_over_probability,
        over_ev=over_ev,
        under_ev=under_ev,
        market_blend_weight=market_weight,
    )


def _pick_side(
    over_probability: float,
    under_probability: float,
    push_probability: float,
    over_ev: Optional[float],
    under_ev: Optional[float],
    ev_selection: bool,
) -> tuple[Optional[str], float]:
    if push_probability > max(over_probability, under_probability):
        return None, 0.0
    if ev_selection and over_ev is not None and under_ev is not None:
        if under_ev > over_ev:
            return "UNDER", under_probability
        return "OVER", over_probability
    if over_probability >= under_probability:
        return "OVER", over_probability
    return "UNDER", under_probability


def _flags(
    features: PlayerFeatures,
    minutes: MinutesProjection,
    pick_side: str,
) -> list[str]:
    flags: list[str] = []
    if minutes.availability_uncertain:
        flags.append("AVAILABILITY_UNCERTAIN")
    if minutes.dnp_probability >= 0.4:
        flags.append("HIGH_DNP_RISK")
    elif minutes.dnp_probability >= 0.2:
        flags.append("DNP_RISK")
    if features.games_played < 8:
        flags.append("THIN_SAMPLE")
    if minutes.projected_minutes < 18.0:
        flags.append("LOW_MINUTES")
    if features.games_last_7_days >= 4:
        flags.append("CONGESTED_SCHEDULE")
    if features.minutes_sd_l10 >= 6.0:
        flags.append("VOLATILE_MINUTES")
    if pick_side == "OVER" and features.trend < -0.01:
        flags.append("NEGATIVE_TREND")
    if pick_side == "UNDER" and features.trend > 0.01:
        flags.append("POSITIVE_TREND")
    return flags
