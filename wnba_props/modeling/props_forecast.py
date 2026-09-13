from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from ..features.player import PlayerFeatures
from ..models import PropLine
from .calibration import ResidualArtifact
from .minutes import MinutesProjection, project_minutes, simulate_prop
from .rates import project_rate
from .value import expected_value_with_push, value_label


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


def forecast_prop(
    *,
    features: PlayerFeatures,
    line: PropLine,
    residuals: ResidualArtifact,
    player_status: str = "",
    league_baseline: Optional[float] = None,
    simulations: int = 10_000,
) -> Optional[PropForecast]:
    minutes = project_minutes(features, player_status=player_status)
    if minutes.availability_excluded:
        return None

    rate = project_rate(features, league_baseline=league_baseline)
    simulation = simulate_prop(
        features=features,
        minutes=minutes,
        projected_rate=rate.projected_rate,
        line=line.line,
        residuals=residuals,
        simulations=simulations,
        seed_material=line.bookmaker,
    )

    pick_side, pick_probability = _pick_side(simulation)
    if pick_side is None:
        return None

    price = line.over_odds if pick_side == "OVER" else line.under_odds
    ev = expected_value_with_push(
        p_win=simulation.over_probability if pick_side == "OVER" else simulation.under_probability,
        p_loss=simulation.under_probability if pick_side == "OVER" else simulation.over_probability,
        price=price,
    )

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
        over_probability=simulation.over_probability,
        under_probability=simulation.under_probability,
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
    )


def _pick_side(simulation) -> tuple[Optional[str], float]:
    if simulation.push_probability > max(
        simulation.over_probability, simulation.under_probability
    ):
        return None, 0.0
    if simulation.over_probability >= simulation.under_probability:
        return "OVER", simulation.over_probability
    return "UNDER", simulation.under_probability


def _flags(
    features: PlayerFeatures,
    minutes: MinutesProjection,
    pick_side: str,
) -> list[str]:
    flags: list[str] = []
    if minutes.availability_uncertain:
        flags.append("AVAILABILITY_UNCERTAIN")
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
