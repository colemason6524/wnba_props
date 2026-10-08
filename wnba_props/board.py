from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace as _replace
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
from .rotation import (
    is_redistributable_out,
    redistribute_out_minutes,
    redistribute_out_minutes_explained,
)
from .modeling.rates import LEAGUE_GAME_TOTAL_BASELINE


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
    snapshot_id: str = ""
    phase: str = "regular"
    sections: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ledger_rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    injury_trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def build_game_rows(
    forecasts: Sequence[GameForecast],
    *,
    run_id: str,
    snapshot_id: str = "",
    phase: str = "regular",
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
                snapshot_id=snapshot_id,
                phase=phase,
                details={
                    "home_probability": forecast.p_home,
                    "away_probability": forecast.p_away,
                    "home_moneyline": forecast.home_moneyline,
                    "away_moneyline": forecast.away_moneyline,
                },
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
                    "injury_debit_pts_home": forecast.injury_debit_pts_home,
                    "injury_debit_pts_away": forecast.injury_debit_pts_away,
                    "margin_pre_debit": forecast.margin_pre_debit,
                    "margin_post_debit": forecast.margin_post_debit,
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
                    snapshot_id=snapshot_id,
                    phase=phase,
                    details={
                        "home_spread_probability": forecast.home_spread_probability,
                        "away_spread_probability": forecast.away_spread_probability,
                        "spread_push_probability": forecast.spread_push_probability,
                        "injury_debit_pts_home": forecast.injury_debit_pts_home,
                        "injury_debit_pts_away": forecast.injury_debit_pts_away,
                        "margin_pre_debit": forecast.margin_pre_debit,
                        "margin_post_debit": forecast.margin_post_debit,
                        "home_spread_price": forecast.spread_home_price,
                        "away_spread_price": forecast.spread_away_price,
                    },
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
                    "injury_debit_pts_home": forecast.injury_debit_pts_home,
                    "injury_debit_pts_away": forecast.injury_debit_pts_away,
                    "total_pre_debit": forecast.total_pre_debit,
                    "total_post_debit": forecast.total_post_debit,
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
                    snapshot_id=snapshot_id,
                    phase=phase,
                    details={
                        "over_probability": forecast.over_probability,
                        "under_probability": forecast.under_probability,
                        "push_probability": forecast.total_push_probability,
                        "injury_debit_pts_home": forecast.injury_debit_pts_home,
                        "injury_debit_pts_away": forecast.injury_debit_pts_away,
                        "total_pre_debit": forecast.total_pre_debit,
                        "total_post_debit": forecast.total_post_debit,
                        "over_price": forecast.over_price,
                        "under_price": forecast.under_price,
                        "market_blend_weight": forecast.market_blend_weight,
                    },
                )
            )

    return rows, ledger


def build_prop_rows(
    forecasts: Sequence[PropForecast],
    *,
    run_id: str,
    snapshot_id: str = "",
    phase: str = "regular",
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
                "price_source_book": forecast.price_source_book,
                "price_fallback": forecast.price_fallback,
                "captured_at": forecast.captured_at,
                "dnp_probability": forecast.dnp_probability,
                "void_probability": forecast.void_probability,
                "rotation_bump": getattr(forecast, "rotation_bump", 0.0),
                "base_minutes": getattr(forecast, "base_minutes", 0.0),
                "pre_bump_minutes": getattr(forecast, "pre_bump_minutes", 0.0),
                "injury_event_ids": list(
                    getattr(forecast, "injury_event_ids", ()) or ()
                ),
                "starter_prob": getattr(forecast, "starter_prob", 0.0),
            }
        )
        ledger.append(
            {
                "snapshot_id": snapshot_id,
                "phase": phase,
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
                "paper_play": forecast.value_label == "playable",
                "source": forecast.market_source,
                "price_source_book": forecast.price_source_book,
                "price_fallback": forecast.price_fallback,
                "captured_at": forecast.captured_at,
                "dnp_probability": forecast.dnp_probability,
                "outcome": PENDING if forecast.price is not None else UNPRICED,
                "units": None,
                "graded": False,
                "over_probability": round(forecast.over_probability, 4),
                "under_probability": round(forecast.under_probability, 4),
                "push_probability": round(forecast.push_probability, 4),
                "over_odds": forecast.over_odds,
                "under_odds": forecast.under_odds,
                "over_ev": None if forecast.over_ev is None else round(forecast.over_ev, 4),
                "under_ev": None if forecast.under_ev is None else round(forecast.under_ev, 4),
                "market_over_probability": forecast.market_over_probability,
                "market_blend_weight": forecast.market_blend_weight,
                "projected_minutes": forecast.projected_minutes,
                "projected_rate": forecast.projected_rate,
                "percentile_10": forecast.percentile_10,
                "percentile_90": forecast.percentile_90,
                "void_probability": forecast.void_probability,
                "flags": list(forecast.flags),
                "rotation_bump": getattr(forecast, "rotation_bump", 0.0),
                "base_minutes": getattr(forecast, "base_minutes", 0.0),
                "pre_bump_minutes": getattr(forecast, "pre_bump_minutes", 0.0),
                "injury_event_ids": list(
                    getattr(forecast, "injury_event_ids", ()) or ()
                ),
                "starter_prob": getattr(forecast, "starter_prob", 0.0),
            }
        )
    return rows, ledger


def assemble_board(
    *,
    screen_date: str,
    run_id: str,
    slot: str = "",
    snapshot_id: str = "",
    phase: str = "regular",
    game_forecasts: Sequence[GameForecast] = (),
    prop_forecasts: Sequence[PropForecast] = (),
    provenance: Optional[Mapping[str, Any]] = None,
) -> ForecastBoard:
    game_rows, game_ledger = build_game_rows(
        game_forecasts, run_id=run_id, snapshot_id=snapshot_id, phase=phase
    )
    prop_rows, prop_ledger = build_prop_rows(
        prop_forecasts, run_id=run_id, snapshot_id=snapshot_id, phase=phase
    )

    sections: dict[str, list[dict[str, Any]]] = {}
    for row in list(game_rows) + list(prop_rows):
        sections.setdefault(row["section"], []).append(row)

    ledger_rows = game_ledger + prop_ledger
    for row in ledger_rows:
        row.setdefault("slot", slot)
    priced = [row for row in ledger_rows if row["price"] is not None]
    primary_priced = [row for row in priced if not row.get("price_fallback", False)]
    fallback_priced = [row for row in priced if row.get("price_fallback", False)]
    favorable = [row for row in ledger_rows if row["value"] == "playable"]

    return ForecastBoard(
        screen_date=screen_date,
        run_id=run_id,
        slot=slot,
        snapshot_id=snapshot_id,
        phase=phase,
        sections=sections,
        ledger_rows=ledger_rows,
        summary={
            "total_rows": len(ledger_rows),
            "priced_rows": len(priced),
            "primary_priced_rows": len(primary_priced),
            "fallback_priced_rows": len(fallback_priced),
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
    snapshot_id: str = "",
    phase: str = "regular",
    market_weight: float = 0.0,
    ev_selection: bool = False,
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
    player_positions: Optional[Mapping[str, str]] = None,
    positional_baselines: Optional[Mapping[tuple[str, str], float]] = None,
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
    player_positions = dict(player_positions or {})
    positional_baselines = dict(positional_baselines or {})
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
                player_statuses=player_statuses,
                logs_by_player=logs_by_player,
            )
            away = build_team_features(
                team=game.away_team,
                opponent=game.home_team,
                game_date=screen_day,
                results=list(team_results),
                is_home=False,
                player_statuses=player_statuses,
                logs_by_player=logs_by_player,
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
                    market_weight=market_weight,
                    ev_selection=ev_selection,
                )
            )

    prop_forecasts: list[PropForecast] = []
    total_by_team: dict[str, float] = {}
    for forecast in game_forecasts:
        total_by_team[forecast.home_team] = forecast.total_projection
        total_by_team[forecast.away_team] = forecast.total_projection
    league_game_total = _league_game_total_baseline(team_results)

    rotation_detail = rotation_minute_detail(
        prop_lines=prop_lines,
        logs_by_player=logs_by_player,
        player_statuses=player_statuses,
        player_positions=player_positions,
    )
    rotation_bumps = {
        norm: bump
        for norm, bump in rotation_detail["bumps"].items()
        if norm in {line.player_name_norm for line in prop_lines}
    }
    injury_events = build_injury_events_for_board(
        screen_date=screen_date,
        rotation_detail=rotation_detail,
        game_forecasts=game_forecasts,
    )
    events_by_recipient: dict[str, list[str]] = {}
    for event in injury_events:
        for recipient in event.get("recipients", ()):
            events_by_recipient.setdefault(recipient, []).append(
                event["event_id"]
            )

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
            player_positions=player_positions,
        )
        if features is None:
            continue
        forecast = forecast_prop(
            features=features,
            line=line,
            residuals=artifact,
            player_status=player_statuses.get(line.player_name_norm, ""),
            rotation_bump=rotation_bumps.get(line.player_name_norm, 0.0),
            league_baseline=league_baselines.get(line.prop_type.upper()),
            positional_baseline=positional_baselines.get(
                (line.prop_type.upper(), features.position)
            )
            if features.position
            else None,
            game_total=total_by_team.get(line.team),
            game_total_baseline=league_game_total,
            simulations=simulations,
            market_weight=market_weight,
            ev_selection=ev_selection,
        )
        if forecast is not None:
            norm = line.player_name_norm
            base_value = rotation_detail["base_minutes"].get(
                norm, getattr(forecast, "pre_bump_minutes", 0.0)
            )
            forecast = _replace(
                forecast,
                base_minutes=round(base_value, 4),
                pre_bump_minutes=round(base_value, 4),
                injury_event_ids=tuple(events_by_recipient.get(norm, ())),
            )
            prop_forecasts.append(forecast)

    board = assemble_board(
        screen_date=screen_date,
        run_id=run_id,
        slot=slot,
        snapshot_id=snapshot_id,
        phase=phase,
        game_forecasts=game_forecasts,
        prop_forecasts=prop_forecasts,
        provenance=provenance,
    )
    board.injury_trace = {
        "screen_date": screen_date,
        "events": injury_events,
        "event_count": len(injury_events),
    }
    return board


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
    primary_priced_rows = int(summary.get("primary_priced_rows", priced_rows))
    fallback_priced_rows = int(summary.get("fallback_priced_rows", 0))
    if require_priced and primary_priced_rows < max(1, min_priced_rows):
        reasons.append(
            f"primary-book priced rows {primary_priced_rows} below minimum "
            f"{max(1, min_priced_rows)}"
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
        "primary_priced_rows": primary_priced_rows,
        "fallback_priced_rows": fallback_priced_rows,
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
    snapshot_id: str = "",
    phase: str = "regular",
    details: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    row = {
        "snapshot_id": snapshot_id,
        "phase": phase,
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
        "paper_play": value == "playable",
        "source": source,
        "captured_at": captured_at,
        "outcome": PENDING if price is not None else UNPRICED,
        "units": None,
        "graded": False,
    }
    if details:
        row.update(dict(details))
    return row


def _format_signed(value: float) -> str:
    return f"{value:+.1f}"


def _rotation_minute_bumps(
    *,
    prop_lines: Sequence[PropLine],
    logs_by_player: Mapping[str, Sequence[PlayerGameLog]],
    player_statuses: Mapping[str, str],
    player_positions: Mapping[str, str],
) -> dict[str, float]:
    """Redistribute OUT players' projected minutes to healthy teammates.

    Runs after the excluded (OUT/IR/suspended) set is implied by
    ``player_statuses`` and before projected minutes feed ``forecast_prop``.
    The roster is sourced from game logs (not prop lines) so an OUT star
    whose line was pulled still vacates minutes. Day-to-day and other
    uncertain statuses never vacate minutes.
    """
    roster_minutes: dict[str, float] = {}
    meta: dict[str, dict[str, object]] = {}
    for norm, logs in logs_by_player.items():
        base = _base_minutes_for_player(logs)
        if base <= 0.0:
            continue
        roster_minutes[norm] = base
        latest = max(logs, key=lambda log: log.game_date)
        meta[norm] = {
            "team": latest.team,
            "position": player_positions.get(norm, ""),
            "role": "starter" if base >= 24.0 else "bench",
            "depth": 0 if base >= 24.0 else 1,
        }
    # Rotation depth within each team: higher base minutes = higher in rotation.
    by_team: dict[str, list[str]] = {}
    for norm in roster_minutes:
        by_team.setdefault(str(meta[norm]["team"]), []).append(norm)
    for team_norms in by_team.values():
        team_norms.sort(key=lambda norm: roster_minutes[norm], reverse=True)
        for depth, norm in enumerate(team_norms):
            meta[norm]["depth"] = depth
    out_entries: list[dict[str, object]] = []
    for norm, status in player_statuses.items():
        if norm not in roster_minutes:
            continue
        if status and is_redistributable_out(status):
            out_entries.append(
                {
                    "player_name_norm": norm,
                    "team": meta[norm]["team"],
                    "status": status,
                    "base_minutes": roster_minutes[norm],
                    "position": meta[norm]["position"],
                }
            )
    if not out_entries:
        return {}
    bumps, _attribution = redistribute_out_minutes_explained(
        out_entries, roster_minutes, meta
    )
    # Only healthy players with a priced line can absorb minutes.
    line_norms = {line.player_name_norm for line in prop_lines}
    return {norm: bump for norm, bump in bumps.items() if norm in line_norms}


def rotation_minute_detail(
    *,
    prop_lines,
    logs_by_player,
    player_statuses,
    player_positions,
) -> dict:
    """Explained rotation detail: bumps, attribution, base minutes, OUT entries.

    Observability wrapper around :func:`redistribute_out_minutes_explained`
    sharing the same roster/base-minute construction as
    :func:`_rotation_minute_bumps`, so bumps stay identical to production.
    """
    roster_minutes: dict[str, float] = {}
    meta: dict[str, dict[str, object]] = {}
    for norm, logs in logs_by_player.items():
        base = _base_minutes_for_player(logs)
        if base <= 0.0:
            continue
        roster_minutes[norm] = base
        latest = max(logs, key=lambda log: log.game_date)
        meta[norm] = {
            "team": latest.team,
            "position": player_positions.get(norm, ""),
            "role": "starter" if base >= 24.0 else "bench",
            "depth": 0 if base >= 24.0 else 1,
        }
    by_team: dict[str, list[str]] = {}
    for norm in roster_minutes:
        by_team.setdefault(str(meta[norm]["team"]), []).append(norm)
    for team_norms in by_team.values():
        team_norms.sort(key=lambda norm: roster_minutes[norm], reverse=True)
        for depth, norm in enumerate(team_norms):
            meta[norm]["depth"] = depth
    out_entries: list[dict[str, object]] = []
    for norm, status in player_statuses.items():
        if norm not in roster_minutes:
            continue
        if status and is_redistributable_out(status):
            out_entries.append(
                {
                    "player_name_norm": norm,
                    "team": meta[norm]["team"],
                    "status": status,
                    "base_minutes": roster_minutes[norm],
                    "position": meta[norm]["position"],
                }
            )
    if not out_entries:
        return {
            "bumps": {},
            "attribution": {},
            "base_minutes": roster_minutes,
            "out_entries": [],
        }
    bumps, attribution = redistribute_out_minutes_explained(
        out_entries, roster_minutes, meta
    )
    return {
        "bumps": bumps,
        "attribution": attribution,
        "base_minutes": roster_minutes,
        "out_entries": out_entries,
    }


def build_injury_events_for_board(
    *,
    screen_date: str,
    rotation_detail: Mapping[str, Any],
    game_forecasts: Sequence[GameForecast] = (),
) -> list[dict[str, Any]]:
    """Build traceable injury events for a board (observability only).

    Team debit context comes from the game forecasts' post-debit totals;
    pre-debit totals are recovered as post + home/away debit.
    """
    from .injury_trace import build_injury_events

    debit: dict[str, float] = {}
    pre_debit: dict[str, float] = {}
    post_debit: dict[str, float] = {}
    for forecast in game_forecasts or ():
        home_debit = float(getattr(forecast, "injury_debit_pts_home", 0.0) or 0.0)
        away_debit = float(getattr(forecast, "injury_debit_pts_away", 0.0) or 0.0)
        total = float(getattr(forecast, "total_projection", 0.0) or 0.0)
        debit[forecast.home_team] = home_debit
        debit[forecast.away_team] = away_debit
        post_debit[forecast.home_team] = total / 2.0
        post_debit[forecast.away_team] = total / 2.0
        pre_debit[forecast.home_team] = total / 2.0 + home_debit
        pre_debit[forecast.away_team] = total / 2.0 + away_debit
    return build_injury_events(
        screen_date=screen_date,
        out_entries=rotation_detail.get("out_entries", ()),
        attribution=rotation_detail.get("attribution", {}),
        vacated_minutes={
            str(entry.get("player_name_norm", "")): float(
                entry.get("base_minutes", 0.0) or 0.0
            )
            for entry in rotation_detail.get("out_entries", ())
        },
        team_debit_pts=debit,
        team_total_pre_debit=pre_debit,
        team_total_post_debit=post_debit,
    )


def _base_minutes_for_player(logs: Sequence[PlayerGameLog]) -> float:
    """Base minutes mirroring ``project_minutes`` 0.65 recency / 0.35 season."""
    eligible = [log for log in logs if log.did_play and log.minutes > 0.0]
    if not eligible:
        return 0.0
    eligible.sort(key=lambda log: log.game_date, reverse=True)
    recent = eligible[:10]
    weights = [0.5 ** (index / 6.0) for index in range(len(recent))]
    recency = sum(log.minutes * w for log, w in zip(recent, weights)) / sum(weights)
    season = sum(log.minutes for log in eligible) / len(eligible)
    return 0.65 * recency + 0.35 * season


def _league_game_total_baseline(team_results: Sequence[TeamGameResult]) -> float:
    totals = [result.team_score + result.opponent_score for result in team_results]
    if not totals:
        return LEAGUE_GAME_TOTAL_BASELINE
    return sum(totals) / len(totals)
