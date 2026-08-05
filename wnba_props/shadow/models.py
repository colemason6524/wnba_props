from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


MODEL_VERSION = "wnba-points-shadow-v1"


@dataclass
class ShadowProjection:
    model_version: str
    created_at: datetime
    screen_date: date
    game_id: str
    game_time: datetime
    player_name: str
    player_name_norm: str
    team: str
    opponent: str
    prop_type: str
    line: float
    bookmaker: str
    line_collected_at: datetime
    over_odds: Optional[int]
    under_odds: Optional[int]
    games_used: int
    last_game_date: date
    projected_minutes: float
    minutes_sd: float
    season_minutes_avg: float
    recent_minutes_avg: float
    season_points_per_minute: float
    recent_points_per_minute: float
    projected_points_per_minute: float
    team_spread: Optional[float]
    game_total: Optional[float]
    game_environment_factor: float
    projected_mean: float
    projected_median: float
    percentile_10: float
    percentile_90: float
    over_probability: float
    under_probability: float
    push_probability: float
    fair_over_odds: Optional[int]
    fair_under_odds: Optional[int]
    over_break_even_probability: Optional[float]
    under_break_even_probability: Optional[float]
    over_expected_value: Optional[float]
    under_expected_value: Optional[float]
    model_side: str
    price_status: str
    decision: str = "RESEARCH_ONLY"
    flags: List[str] = field(default_factory=list)
