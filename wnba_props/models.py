from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


@dataclass
class Game:
    game_id: str
    game_date: date
    game_time: datetime
    home_team: str
    away_team: str
    source: str


@dataclass
class PropLine:
    event_id: str
    game_date: date
    player_name_raw: str
    player_name_norm: str
    team: str
    opponent: str
    prop_type: str
    line: float
    bookmaker: str
    source: str
    collected_at: datetime


@dataclass
class PlayerGameLog:
    player_name_raw: str
    player_name_norm: str
    game_date: date
    team: str
    opponent: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    threes_made: int
    did_play: bool
    source: str

    @property
    def pra(self) -> int:
        return self.points + self.rebounds + self.assists

    @property
    def points_assists(self) -> int:
        return self.points + self.assists

    @property
    def points_rebounds(self) -> int:
        return self.points + self.rebounds

    @property
    def rebounds_assists(self) -> int:
        return self.rebounds + self.assists


@dataclass
class Candidate:
    player_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    bookmaker: str
    hits_last_5: int
    played_last_5: int
    hits_last_10: int
    played_last_10: int
    avg_last_5: float
    avg_last_10: float
    median_last_5: float
    median_last_10: float
    season_avg: float
    avg_minutes_last_5: float
    avg_minutes_last_10: float
    delta_avg_last_5: float
    score: int
    flags: List[str] = field(default_factory=list)
    spread: Optional[float] = None
    total: Optional[float] = None
    opp_avg: Optional[float] = None


@dataclass
class ScreeningResult:
    candidates: List[Candidate]
    evaluated_prop_lines: int
    non_qualifying_prop_lines: int


@dataclass
class TeamInjury:
    player_name: str
    player_name_norm: str
    team: str
    status: str
    note: str = ""
    injury_type: str = ""
    return_date: Optional[str] = None
    avg_minutes_last_10: Optional[float] = None
    season_points: Optional[float] = None
    season_rebounds: Optional[float] = None
    season_assists: Optional[float] = None
    impact_score: Optional[float] = None
    impact_level: str = "minor"
