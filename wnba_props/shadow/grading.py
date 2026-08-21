from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any

from ..utils import normalize_name
from .collection import capture_lead_minutes, projection_id
from .pricing import settled_profit_units
from .sources import ShadowBoxscoreStatLine, ShadowGameStatus


def grade_shadow_projections(
    projections: list[dict[str, Any]],
    *,
    game_statuses: dict[str, ShadowGameStatus],
    boxscores: dict[str, dict[tuple[str, str], ShadowBoxscoreStatLine]],
) -> dict[str, Any]:
    """Resolve snapshot projections without treating pending or ambiguous rows as losses."""
    graded: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    voided: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for projection in projections:
        identity = _projection_identity(projection)
        game_id = identity["game_id"]
        status = game_statuses.get(game_id)
        if status is None:
            unresolved.append({**identity, "reason": "game_status_missing"})
            continue
        if not status.completed:
            pending.append(
                {
                    **identity,
                    "reason": "game_not_final",
                    "game_state": status.state,
                    "game_status_detail": status.detail,
                }
            )
            continue

        boxscore = boxscores.get(game_id)
        if boxscore is None:
            unresolved.append({**identity, "reason": "final_boxscore_unavailable"})
            continue

        player_key = str(projection.get("player_name_norm", "")).strip()
        if not player_key:
            player_key = normalize_name(str(projection.get("player_name", "")))
        stat_line = boxscore.get((player_key, identity["team"]))
        if stat_line is None:
            unresolved.append({**identity, "reason": "final_boxscore_player_missing"})
            continue
        if stat_line.minutes <= 0.0:
            voided.append(
                {
                    **identity,
                    "reason": "void_dnp",
                    "actual_minutes": stat_line.minutes,
                    "actual_points": stat_line.points,
                }
            )
            continue

        graded.append(_grade_projection(projection, stat_line))

    return {
        "graded": graded,
        "pending": pending,
        "voided": voided,
        "unresolved": unresolved,
        "summary": summarize_grades(graded, pending, voided, unresolved),
        "over_probability_calibration": calibration_buckets(graded),
    }


def summarize_grades(
    graded: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    voided: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> dict[str, Any]:
    selected = [row for row in graded if row["model_side"] in {"OVER", "UNDER"}]
    selected_decisions = [row for row in selected if row["selection_outcome"] != "push"]
    priced = [row for row in selected if row["profit_units"] is not None]
    price_evaluable = [row for row in selected if row["price_evaluable"]]
    unpriced = [row for row in selected if not row["price_evaluable"]]
    passed = [row for row in graded if row["model_side"] not in {"OVER", "UNDER"}]
    brier_rows = [row for row in graded if row["over_brier_score"] is not None]

    wins = sum(1 for row in selected if row["selection_outcome"] == "win")
    losses = sum(1 for row in selected if row["selection_outcome"] == "loss")
    pushes = sum(1 for row in selected if row["selection_outcome"] == "push")
    units = sum(float(row["profit_units"]) for row in priced)

    return {
        "graded_count": len(graded),
        "pending_count": len(pending),
        "void_dnp_count": len(voided),
        "unresolved_count": len(unresolved),
        "mean_absolute_error_points": _rounded_mean(
            [float(row["absolute_error_points"]) for row in graded]
        ),
        "root_mean_squared_error_points": _rounded_rmse(
            [float(row["projection_error_points"]) for row in graded]
        ),
        "mean_absolute_error_minutes": _rounded_mean(
            [float(row["absolute_error_minutes"]) for row in graded]
        ),
        "over_brier_score": _rounded_mean(
            [float(row["over_brier_score"]) for row in brier_rows]
        ),
        "over_brier_score_unconditional": _rounded_mean(
            [float(row["over_brier_score_unconditional"]) for row in brier_rows]
        ),
        "selected_count": len(selected),
        "selected_wins": wins,
        "selected_losses": losses,
        "selected_pushes": pushes,
        "selected_hit_rate": round(wins / len(selected_decisions), 4) if selected_decisions else None,
        "price_evaluable_selected_count": len(price_evaluable),
        "unpriced_selected_count": len(unpriced),
        "pass_count": len(passed),
        "priced_selected_count": len(priced),
        "flat_stake_units": round(units, 4) if priced else None,
        "flat_stake_roi": round(units / len(priced), 4) if priced else None,
    }


def calibration_buckets(graded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in graded:
        if row["actual_over"] is None:
            continue
        probability = min(
            1.0,
            max(
                0.0,
                float(row.get("conditional_over_probability", row["over_probability"])),
            ),
        )
        bucket_index = min(9, int(probability * 10.0))
        buckets[bucket_index].append(row)

    result: list[dict[str, Any]] = []
    for bucket_index in sorted(buckets):
        rows = buckets[bucket_index]
        lower = bucket_index / 10.0
        upper = (bucket_index + 1) / 10.0
        result.append(
            {
                "bucket": f"{lower:.1f}-{upper:.1f}",
                "count": len(rows),
                "average_predicted_over_probability": round(
                    mean(
                        float(row.get("conditional_over_probability", row["over_probability"]))
                        for row in rows
                    ),
                    4,
                ),
                "actual_over_rate": round(
                    mean(1.0 if row["actual_over"] else 0.0 for row in rows), 4
                ),
            }
        )
    return result


def render_grading_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "WNBA PTS Shadow Grade (research only)",
        f"Source snapshot: {report['source_snapshot']}",
        f"Graded: {summary['graded_count']}",
        f"Pending: {summary['pending_count']}",
        f"Voided DNP: {summary['void_dnp_count']}",
        f"Unresolved: {summary['unresolved_count']}",
    ]
    if summary["graded_count"]:
        lines.extend(
            [
                f"Points MAE: {_display(summary['mean_absolute_error_points'])}",
                f"Points RMSE: {_display(summary['root_mean_squared_error_points'])}",
                f"Minutes MAE: {_display(summary['mean_absolute_error_minutes'])}",
                f"Over-probability Brier: {_display(summary['over_brier_score'])}",
                (
                    "Research selections: "
                    f"{summary['selected_count']} "
                    f"({summary['selected_wins']}-{summary['selected_losses']}-"
                    f"{summary['selected_pushes']})"
                ),
                f"Priced selections: {summary['priced_selected_count']}",
                f"Flat-stake units: {_display_signed(summary['flat_stake_units'])}",
                f"Flat-stake ROI: {_display_percent(summary['flat_stake_roi'])}",
            ]
        )
    lines.extend(
        [
            "",
            "These are shadow diagnostics, not evidence of a betting edge.",
            "Pending, DNP, and unresolved records are not counted as losses.",
        ]
    )
    return "\n".join(lines)


def _grade_projection(
    projection: dict[str, Any],
    stat_line: ShadowBoxscoreStatLine,
) -> dict[str, Any]:
    identity = _projection_identity(projection)
    line = float(projection["line"])
    actual_points = stat_line.points
    actual_outcome = "over" if actual_points > line else "under" if actual_points < line else "push"
    model_side = str(projection.get("model_side", "PASS")).upper()
    selection_outcome = _selection_outcome(model_side, actual_outcome)
    selected_odds = _selected_odds(model_side, projection)
    projected_mean = float(projection["projected_mean"])
    projected_minutes = float(projection["projected_minutes"])
    points_error = actual_points - projected_mean
    minutes_error = stat_line.minutes - projected_minutes
    over_probability = float(projection["over_probability"])
    conditional_over = _conditional_over_probability(projection)
    actual_over = None if actual_outcome == "push" else actual_outcome == "over"
    brier = None if actual_over is None else (conditional_over - float(actual_over)) ** 2
    brier_unconditional = None if actual_over is None else (over_probability - float(actual_over)) ** 2
    capture_lead = _capture_lead(projection)

    return {
        **identity,
        "projection_id": projection.get("projection_id") or projection_id(projection),
        "model_version": projection.get("model_version"),
        "game_time": projection.get("game_time"),
        "line_collected_at": projection.get("line_collected_at"),
        "capture_lead_minutes": capture_lead,
        "captured_before_scheduled_start": (
            capture_lead is not None and capture_lead > 0.0
        ),
        "over_odds": projection.get("over_odds"),
        "under_odds": projection.get("under_odds"),
        "price_status": projection.get("price_status"),
        "projected_mean": projected_mean,
        "projected_minutes": projected_minutes,
        "percentile_10": projection.get("percentile_10"),
        "percentile_90": projection.get("percentile_90"),
        "team_spread": projection.get("team_spread"),
        "game_total": projection.get("game_total"),
        "flags": list(projection.get("flags", [])),
        "over_probability": over_probability,
        "under_probability": float(projection["under_probability"]),
        "push_probability": float(projection.get("push_probability", 0.0)),
        "conditional_over_probability": round(conditional_over, 6),
        "model_side": model_side,
        "decision": projection.get("decision", "RESEARCH_ONLY"),
        "actual_minutes": round(stat_line.minutes, 4),
        "actual_points": actual_points,
        "actual_prop_outcome": actual_outcome,
        "actual_over": actual_over,
        "projection_error_points": round(points_error, 4),
        "absolute_error_points": round(abs(points_error), 4),
        "projection_error_minutes": round(minutes_error, 4),
        "absolute_error_minutes": round(abs(minutes_error), 4),
        "over_brier_score": round(brier, 6) if brier is not None else None,
        "over_brier_score_unconditional": (
            round(brier_unconditional, 6) if brier_unconditional is not None else None
        ),
        "selection_outcome": selection_outcome,
        "selected_odds": selected_odds,
        "price_evaluable": selected_odds is not None,
        "profit_units": _rounded_optional(settled_profit_units(selection_outcome, selected_odds)),
        "resolution_source": "espn_final_boxscore_shadow",
    }


def _projection_identity(projection: dict[str, Any]) -> dict[str, Any]:
    return {
        "screen_date": projection.get("screen_date"),
        "game_id": str(projection.get("game_id", "")),
        "player_name": projection.get("player_name"),
        "player_name_norm": projection.get("player_name_norm"),
        "team": str(projection.get("team", "")),
        "opponent": projection.get("opponent"),
        "prop_type": projection.get("prop_type"),
        "line": float(projection.get("line", 0.0)),
        "bookmaker": projection.get("bookmaker"),
    }


def _selection_outcome(model_side: str, actual_outcome: str) -> str:
    if model_side not in {"OVER", "UNDER"}:
        return "pass"
    if actual_outcome == "push":
        return "push"
    if model_side.lower() == actual_outcome:
        return "win"
    return "loss"


def _selected_odds(model_side: str, projection: dict[str, Any]) -> int | None:
    raw = projection.get("over_odds") if model_side == "OVER" else projection.get("under_odds")
    if model_side not in {"OVER", "UNDER"} or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _rounded_rmse(values: list[float]) -> float | None:
    if not values:
        return None
    return round(math.sqrt(mean(value * value for value in values)), 4)


def _rounded_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _conditional_over_probability(projection: dict[str, Any]) -> float:
    raw = projection.get("conditional_over_probability")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    over = float(projection.get("over_probability", 0.0))
    under = float(projection.get("under_probability", 0.0))
    non_push = over + under
    if non_push > 0.0:
        return over / non_push
    return 0.5


def _capture_lead(projection: dict[str, Any]) -> float | None:
    raw = projection.get("capture_lead_minutes")
    if raw is not None:
        try:
            return round(float(raw), 2)
        except (TypeError, ValueError):
            return None
    game_time = projection.get("game_time")
    collected_at = projection.get("line_collected_at")
    if not game_time or not collected_at:
        return None
    try:
        return capture_lead_minutes(
            datetime.fromisoformat(str(game_time)),
            datetime.fromisoformat(str(collected_at)),
        )
    except ValueError:
        return None


def _display(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _display_signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.4f}"


def _display_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.2f}%"
