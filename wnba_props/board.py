from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .features.player import build_player_features
from .features.team import TeamFeatures, TeamGameResult, build_team_features
from .grading import proposition_id
from .ledger import PENDING, UNPRICED, append_rows
from .models import Game, PlayerGameLog, PropLine
from .modeling.calibration import ResidualArtifact
from .modeling.game_forecast import (
    GameEngine,
    GameForecast,
    GameMarket,
    forecast_game,
)
from .modeling.props_forecast import PropForecast, forecast_prop


PROP_SECTIONS = {
    "PTS": "Points",
    "REB": "Rebounds",
    "AST": "Assists",
    "3PM": "Three-Pointers",
}


@dataclass
class ForecastBoard:
    screen_date: str
    run_id: str
    slot: str = ""
    sections: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ledger_rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def build_game_rows(
    forecasts: Sequence[GameForecast],
    *,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    for forecast in forecasts:
        matchup = f"{forecast.away_team} @ {forecast.home_team}"
        date_text = forecast.game_date

        winner_team = (
            forecast.home_team if forecast.winner_pick == "HOME" else forecast.away_team
        )
        rows.append(
            {
                "section": "Moneyline",
                "subject": matchup,
                "pick_label": winner_team,
                "line": None,
                "price": forecast.winner_price,
                "p_pick": forecast.winner_probability,
                "model_probability": forecast.winner_probability,
                "projection": None,
                "ev": forecast.winner_ev,
                "value": forecast.winner_value,
                "source": forecast.moneyline_source,
                "captured_at": forecast.captured_at,
            }
        )
        ledger.append(
            _ledger_row(
                run_id=run_id,
                market="ML",
                subject=matchup,
                game_date=date_text,
                line=None,
                pick=forecast.winner_pick,
                pick_label=winner_team,
                price=forecast.winner_price,
                ev=forecast.winner_ev,
                value=forecast.winner_value,
                probability=forecast.winner_probability,
                projection=None,
                source=forecast.moneyline_source,
                captured_at=forecast.captured_at,
            )
        )

        if forecast.spread_pick is not None and forecast.spread_line is not None:
            spread_team = (
                forecast.home_team
                if forecast.spread_pick == "HOME"
                else forecast.away_team
            )
            team_spread = (
                forecast.spread_line
                if forecast.spread_pick == "HOME"
                else -forecast.spread_line
            )
            rows.append(
                {
                    "section": "Spread",
                    "subject": matchup,
                    "pick_label": f"{spread_team} {_format_signed(team_spread)}",
                    "line": None,
                    "price": forecast.spread_price,
                    "p_pick": forecast.spread_probability,
                    "model_probability": forecast.spread_probability,
                    "projection": forecast.margin_projection,
                    "ev": forecast.spread_ev,
                    "value": forecast.spread_value,
                    "source": forecast.spread_source,
                    "captured_at": forecast.captured_at,
                }
            )
            ledger.append(
                _ledger_row(
                    run_id=run_id,
                    market="SPREAD",
                    subject=matchup,
                    game_date=date_text,
                    line=forecast.spread_line,
                    pick=forecast.spread_pick,
                    pick_label=f"{spread_team} {_format_signed(team_spread)}",
                    price=forecast.spread_price,
                    ev=forecast.spread_ev,
                    value=forecast.spread_value,
                    probability=forecast.spread_probability,
                    projection=forecast.margin_projection,
                    source=forecast.spread_source,
                    captured_at=forecast.captured_at,
                )
            )

        if forecast.total_pick is not None and forecast.total_line is not None:
            rows.append(
                {
                    "section": "Totals",
                    "subject": matchup,
                    "pick_label": forecast.total_pick.title(),
                    "line": forecast.total_line,
                    "price": forecast.total_price,
                    "p_pick": forecast.total_probability,
                    "model_probability": forecast.total_probability,
                    "projection": forecast.total_projection,
                    "ev": forecast.total_ev,
                    "value": forecast.total_value,
                    "source": forecast.total_source,
                    "captured_at": forecast.captured_at,
                }
            )
            ledger.append(
                _ledger_row(
                    run_id=run_id,
                    market="TOTAL",
                    subject=matchup,
                    game_date=date_text,
                    line=forecast.total_line,
                    pick=forecast.total_pick,
                    pick_label=forecast.total_pick.title(),
                    price=forecast.total_price,
                    ev=forecast.total_ev,
                    value=forecast.total_value,
                    probability=forecast.total_probability,
                    projection=forecast.total_projection,
                    source=forecast.total_source,
                    captured_at=forecast.captured_at,
                )
            )

    return rows, ledger


def build_prop_rows(
    forecasts: Sequence[PropForecast],
    *,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    for forecast in forecasts:
        section = PROP_SECTIONS.get(forecast.prop_type)
        if section is None:
            continue
        pick_label = forecast.pick_side.title()
        rows.append(
            {
                "section": section,
                "subject": forecast.player_name_raw,
                "pick_label": pick_label,
                "line": forecast.line,
                "price": forecast.price,
                "p_pick": forecast.pick_probability,
                "model_probability": forecast.pick_probability,
                "projection": forecast.projected_mean,
                "ev": forecast.ev,
                "value": forecast.value_label,
                "source": forecast.market_source,
                "captured_at": forecast.captured_at,
            }
        )
        ledger.append(
            {
                "run_id": run_id,
                "proposition_id": proposition_id(
                    forecast.prop_type,
                    forecast.player_name_norm,
                    forecast.game_date.isoformat(),
                    forecast.line,
                ),
                "market": forecast.prop_type,
                "subject": forecast.player_name_raw,
                "game_date": forecast.game_date.isoformat(),
                "line": forecast.line,
                "pick": forecast.pick_side,
                "price": forecast.price,
                "probability": round(forecast.pick_probability, 4),
                "projection": round(forecast.projected_mean, 2),
                "ev": None if forecast.ev is None else round(forecast.ev, 4),
                "value": forecast.value_label,
                "source": forecast.market_source,
                "captured_at": forecast.captured_at,
                "outcome": PENDING if forecast.price is not None else UNPRICED,
                "units": None,
                "graded": False,
            }
        )
    return rows, ledger


def assemble_board(
    *,
    screen_date: str,
    run_id: str,
    slot: str = "",
    game_forecasts: Sequence[GameForecast] = (),
    prop_forecasts: Sequence[PropForecast] = (),
    provenance: Optional[Mapping[str, Any]] = None,
) -> ForecastBoard:
    game_rows, game_ledger = build_game_rows(game_forecasts, run_id=run_id)
    prop_rows, prop_ledger = build_prop_rows(prop_forecasts, run_id=run_id)

    sections: dict[str, list[dict[str, Any]]] = {}
    for row in list(game_rows) + list(prop_rows):
        sections.setdefault(row["section"], []).append(row)

    ledger_rows = game_ledger + prop_ledger
    priced = [row for row in ledger_rows if row["price"] is not None]
    favorable = [row for row in ledger_rows if row["value"] == "FAVORABLE"]

    return ForecastBoard(
        screen_date=screen_date,
        run_id=run_id,
        slot=slot,
        sections=sections,
        ledger_rows=ledger_rows,
        summary={
            "total_rows": len(ledger_rows),
            "priced_rows": len(priced),
            "favorable_rows": len(favorable),
            "game_rows": len(game_rows),
            "prop_rows": len(prop_rows),
        },
        provenance=dict(provenance or {}),
    )


def build_daily_board(
    *,
    screen_date: str,
    run_id: str,
    slot: str = "",
    slate: Sequence[Game] = (),
    prop_lines: Sequence[PropLine] = (),
    league_logs: Sequence[PlayerGameLog] = (),
    team_results: Sequence[TeamGameResult] = (),
    game_engine: Optional[GameEngine] = None,
    residual_artifacts: Optional[Mapping[str, ResidualArtifact]] = None,
    game_markets: Optional[Mapping[tuple[str, str], GameMarket]] = None,
    market_provenance: Optional[Mapping[tuple[str, str], Any]] = None,
    player_statuses: Optional[Mapping[str, str]] = None,
    league_baselines: Optional[Mapping[str, float]] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    simulations: int = 10_000,
) -> ForecastBoard:
    """Collect forecasts for a slate and assemble the daily board."""
    from datetime import date as _date

    screen_day = _date.fromisoformat(screen_date)
    game_markets = dict(game_markets or {})
    market_provenance = dict(market_provenance or {})
    player_statuses = dict(player_statuses or {})
    league_baselines = dict(league_baselines or {})
    residual_artifacts = dict(residual_artifacts or {})

    logs_by_player: dict[str, list[PlayerGameLog]] = {}
    for log in league_logs:
        logs_by_player.setdefault(log.player_name_norm, []).append(log)

    game_forecasts: list[GameForecast] = []
    if game_engine is not None:
        for game in slate:
            home = build_team_features(
                team=game.home_team,
                opponent=game.away_team,
                game_date=screen_day,
                results=list(team_results),
                is_home=True,
            )
            away = build_team_features(
                team=game.away_team,
                opponent=game.home_team,
                game_date=screen_day,
                results=list(team_results),
                is_home=False,
            )
            if home is None or away is None:
                continue
            market = game_markets.get((game.away_team, game.home_team), GameMarket())
            snap = market_provenance.get((game.away_team, game.home_team))
            game_forecasts.append(
                forecast_game(
                    home=home,
                    away=away,
                    engine=game_engine,
                    market=market,
                    moneyline_source=getattr(snap, "moneyline_source", "") or "",
                    spread_source=getattr(snap, "spread_source", "") or "",
                    total_source=getattr(snap, "total_source", "") or "",
                    captured_at=getattr(snap, "captured_at", "") or "",
                )
            )

    prop_forecasts: list[PropForecast] = []
    for line in prop_lines:
        artifact = residual_artifacts.get(line.prop_type.upper())
        if artifact is None:
            continue
        features = build_player_features(
            logs=logs_by_player.get(line.player_name_norm, []),
            league_logs=list(league_logs),
            player_name_norm=line.player_name_norm,
            player_name_raw=line.player_name_raw,
            team=line.team,
            opponent=line.opponent,
            game_date=line.game_date,
            prop_type=line.prop_type,
        )
        if features is None:
            continue
        forecast = forecast_prop(
            features=features,
            line=line,
            residuals=artifact,
            player_status=player_statuses.get(line.player_name_norm, ""),
            league_baseline=league_baselines.get(line.prop_type.upper()),
            simulations=simulations,
        )
        if forecast is not None:
            prop_forecasts.append(forecast)

    return assemble_board(
        screen_date=screen_date,
        run_id=run_id,
        slot=slot,
        game_forecasts=game_forecasts,
        prop_forecasts=prop_forecasts,
        provenance=provenance,
    )


def write_board(path: Path, board: ForecastBoard) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(board)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def health_report(
    board: ForecastBoard,
    *,
    required_sections: Sequence[str] = (),
    require_priced: bool = False,
    min_priced_rows: int = 1,
    slate_games: int = 0,
    evaluated_lines: int = 0,
    min_evaluated_lines: int = 0,
    min_event_match_ratio: Optional[float] = None,
    min_player_load_ratio: Optional[float] = None,
    games_with_markets: int = 0,
) -> dict[str, Any]:
    """Report whether the board is complete enough to publish.

    Publication fails closed: when a configured coverage threshold is not met
    the status is ``degraded`` and the reasons explain the shortfall.
    """
    reasons: list[str] = []
    for section in required_sections:
        if not board.sections.get(section):
            reasons.append(f"missing required section: {section}")
    if not board.ledger_rows:
        reasons.append("board has no rows")

    summary = board.summary
    priced_rows = int(summary.get("priced_rows", 0))
    if require_priced and priced_rows < max(1, min_priced_rows):
        reasons.append(
            f"priced rows {priced_rows} below minimum {max(1, min_priced_rows)}"
        )

    if min_evaluated_lines and evaluated_lines < min_evaluated_lines:
        reasons.append(
            f"evaluated lines {evaluated_lines} below minimum {min_evaluated_lines}"
        )

    if slate_games and min_event_match_ratio is not None:
        ratio = games_with_markets / slate_games
        if ratio < min_event_match_ratio:
            reasons.append(
                f"game-market coverage {games_with_markets}/{slate_games} "
                f"({ratio:.0%}) below minimum {min_event_match_ratio:.0%}"
            )

    if evaluated_lines and min_player_load_ratio is not None:
        prop_rows = int(summary.get("prop_rows", 0))
        ratio = prop_rows / evaluated_lines
        if ratio < min_player_load_ratio:
            reasons.append(
                f"player coverage {prop_rows}/{evaluated_lines} "
                f"({ratio:.0%}) below minimum {min_player_load_ratio:.0%}"
            )

    return {
        "status": "ok" if not reasons else "degraded",
        "reasons": reasons,
        "games_with_markets": games_with_markets,
        "slate_games": slate_games,
        "evaluated_lines": evaluated_lines,
        "priced_rows": priced_rows,
    }


def write_ledger(path: Path, board: ForecastBoard) -> int:
    return append_rows(path, board.ledger_rows)


def load_prop_artifacts(
    artifact_dir: Path,
    prop_types: Sequence[str] = ("PTS", "REB", "AST", "3PM"),
) -> dict[str, ResidualArtifact]:
    from .modeling.calibration import load_residual_artifact

    artifacts: dict[str, ResidualArtifact] = {}
    for prop_type in prop_types:
        path = artifact_dir / f"{prop_type.lower()}_engine_artifact.json"
        if path.exists():
            artifacts[prop_type.upper()] = load_residual_artifact(path)
    return artifacts


def _ledger_row(
    *,
    run_id: str,
    market: str,
    subject: str,
    game_date: str,
    line: Optional[float],
    pick: Optional[str],
    pick_label: str,
    price: Optional[int],
    ev: Optional[float],
    value: str,
    probability: Optional[float],
    projection: Optional[float] = None,
    source: str = "",
    captured_at: str = "",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "proposition_id": proposition_id(market, subject, game_date, line),
        "market": market,
        "subject": subject,
        "game_date": game_date,
        "line": line,
        "pick": pick,
        "pick_label": pick_label,
        "price": price,
        "probability": None if probability is None else round(probability, 4),
        "projection": None if projection is None else round(projection, 2),
        "ev": None if ev is None else round(ev, 4),
        "value": value,
        "source": source,
        "captured_at": captured_at,
        "outcome": PENDING if price is not None else UNPRICED,
        "units": None,
        "graded": False,
    }


def _format_signed(value: float) -> str:
    return f"{value:+.1f}"
