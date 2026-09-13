from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from ..features.team import TeamFeatures
from .game import (
    LogisticModel,
    RidgeModel,
    margin_vector,
    total_vector,
    winner_vector,
)
from .value import expected_value, expected_value_with_push, value_label


GAME_ENGINE_VERSION = "wnba-game-engine-v1"


@dataclass
class GameEngine:
    version: str = GAME_ENGINE_VERSION
    winner: LogisticModel = field(default_factory=LogisticModel)
    margin: RidgeModel = field(default_factory=RidgeModel)
    total: RidgeModel = field(default_factory=RidgeModel)
    trained_games: int = 0
    feature_means: dict[str, float] = field(default_factory=dict)
    trained_through: str = ""
    feature_version: str = "wnba-game-features-v1"
    fit_at: str = ""
    code_commit: str = ""
    sha256: str = ""


@dataclass(frozen=True)
class GameMarket:
    home_moneyline: Optional[int] = None
    away_moneyline: Optional[int] = None
    home_spread: Optional[float] = None
    home_spread_price: Optional[int] = None
    away_spread_price: Optional[int] = None
    total_line: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None


@dataclass(frozen=True)
class GameForecast:
    home_team: str
    away_team: str
    game_date: str
    p_home: float
    p_away: float
    margin_projection: float
    total_projection: float
    winner_pick: str
    winner_probability: float
    winner_price: Optional[int]
    winner_ev: Optional[float]
    winner_value: str
    spread_pick: Optional[str]
    spread_probability: Optional[float]
    spread_price: Optional[int]
    spread_ev: Optional[float]
    spread_value: str
    spread_line: Optional[float]
    total_pick: Optional[str]
    total_probability: Optional[float]
    total_price: Optional[int]
    total_ev: Optional[float]
    total_value: str
    total_line: Optional[float]
    moneyline_source: str = ""
    spread_source: str = ""
    total_source: str = ""
    captured_at: str = ""


def save_game_engine(path: Path, engine: GameEngine) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": engine.version,
        "trained_games": engine.trained_games,
        "trained_through": engine.trained_through,
        "feature_version": engine.feature_version,
        "fit_at": engine.fit_at or datetime.now(timezone.utc).isoformat(),
        "code_commit": engine.code_commit,
        "feature_means": engine.feature_means,
        "winner": asdict(engine.winner),
        "margin": asdict(engine.margin),
        "total": asdict(engine.total),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def load_game_engine(path: Path) -> GameEngine:
    import hashlib

    payload = json.loads(path.read_text())
    if payload.get("version") != GAME_ENGINE_VERSION:
        raise ValueError(
            f"unsupported game engine version: {payload.get('version')!r}"
        )
    return GameEngine(
        version=payload["version"],
        trained_games=int(payload.get("trained_games", 0)),
        feature_means=dict(payload.get("feature_means", {})),
        trained_through=str(payload.get("trained_through", "")),
        feature_version=str(payload.get("feature_version", "")),
        fit_at=str(payload.get("fit_at", "")),
        code_commit=str(payload.get("code_commit", "")),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        winner=LogisticModel(**payload["winner"]),
        margin=RidgeModel(**payload["margin"]),
        total=RidgeModel(**payload["total"]),
    )


def forecast_game(
    *,
    home: TeamFeatures,
    away: TeamFeatures,
    engine: GameEngine,
    market: GameMarket,
    moneyline_source: str = "",
    spread_source: str = "",
    total_source: str = "",
    captured_at: str = "",
) -> GameForecast:
    p_home = engine.winner.predict_proba(winner_vector(home, away))
    p_away = 1.0 - p_home

    margin_projection = engine.margin.predict(margin_vector(home, away))
    total_projection = engine.total.predict(total_vector(home, away))
    margin_sd = engine.margin.residual_sd or 10.0
    total_sd = engine.total.residual_sd or 12.0

    winner_pick = "HOME" if p_home >= p_away else "AWAY"
    winner_probability = p_home if winner_pick == "HOME" else p_away
    winner_price = (
        market.home_moneyline if winner_pick == "HOME" else market.away_moneyline
    )
    winner_ev = expected_value(winner_probability, winner_price)

    spread_pick = None
    spread_probability: Optional[float] = None
    spread_price: Optional[int] = None
    spread_ev: Optional[float] = None
    if market.home_spread is not None:
        over, under, push = _split_probability(
            margin_projection, margin_sd, -market.home_spread
        )
        cover = over
        no_cover = under
        if cover >= no_cover:
            spread_pick = "HOME"
            spread_probability = cover
            spread_price = market.home_spread_price
            spread_ev = expected_value_with_push(cover, no_cover, spread_price)
        else:
            spread_pick = "AWAY"
            spread_probability = no_cover
            spread_price = market.away_spread_price
            spread_ev = expected_value_with_push(no_cover, cover, spread_price)

    total_pick = None
    total_probability: Optional[float] = None
    total_price: Optional[int] = None
    total_ev: Optional[float] = None
    if market.total_line is not None:
        over, under, push = _split_probability(
            total_projection, total_sd, market.total_line
        )
        if over >= under:
            total_pick = "OVER"
            total_probability = over
            total_price = market.over_price
            total_ev = expected_value_with_push(over, under, total_price)
        else:
            total_pick = "UNDER"
            total_probability = under
            total_price = market.under_price
            total_ev = expected_value_with_push(under, over, total_price)

    return GameForecast(
        home_team=home.team,
        away_team=away.team,
        game_date=home.game_date.isoformat(),
        p_home=p_home,
        p_away=p_away,
        margin_projection=margin_projection,
        total_projection=total_projection,
        winner_pick=winner_pick,
        winner_probability=winner_probability,
        winner_price=winner_price,
        winner_ev=winner_ev,
        winner_value=value_label(winner_ev),
        spread_pick=spread_pick,
        spread_probability=spread_probability,
        spread_price=spread_price,
        spread_ev=spread_ev,
        spread_value=value_label(spread_ev) if spread_ev is not None else "UNPRICED",
        spread_line=market.home_spread,
        total_pick=total_pick,
        total_probability=total_probability,
        total_price=total_price,
        total_ev=total_ev,
        total_value=value_label(total_ev) if total_ev is not None else "UNPRICED",
        total_line=market.total_line,
        moneyline_source=moneyline_source,
        spread_source=spread_source,
        total_source=total_source,
        captured_at=captured_at,
    )


def _split_probability(
    mean: float,
    sd: float,
    line: float,
) -> tuple[float, float, float]:
    if sd <= 0.0:
        if mean > line:
            return 1.0, 0.0, 0.0
        if mean < line:
            return 0.0, 1.0, 0.0
        return 0.0, 0.0, 1.0

    is_integer = abs(line - round(line)) < 1e-9
    if is_integer:
        lower = _normal_cdf(line - 0.5, mean, sd)
        upper = _normal_cdf(line + 0.5, mean, sd)
        push = max(0.0, upper - lower)
        over = max(0.0, 1.0 - upper)
        under = max(0.0, lower)
        return over, under, push
    over = 1.0 - _normal_cdf(line, mean, sd)
    under = _normal_cdf(line, mean, sd)
    return over, under, 0.0


def _normal_cdf(value: float, mean: float, sd: float) -> float:
    if sd <= 0.0:
        return 1.0 if value >= mean else 0.0
    z = (value - mean) / sd
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
