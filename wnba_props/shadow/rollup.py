from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from .grading import calibration_buckets
from .pricing import implied_probability, is_valid_price


EVIDENCE_TARGETS = {
    "minimum_slates": 7,
    "minimum_games": 20,
    "minimum_projections": 100,
    "minimum_both_side_price_rate": 0.90,
}


def build_shadow_rollup(
    reports: list[dict[str, Any]],
    *,
    minimum_lead_minutes: float = 20.0,
    maximum_lead_minutes: float = 90.0,
) -> dict[str, Any]:
    all_rows = [row for report in reports for row in report.get("graded", [])]
    primary_candidates = []
    exclusions: Counter[str] = Counter()
    for row in all_rows:
        audit_reason = _audit_status(row)
        if audit_reason is not None:
            exclusions[audit_reason] += 1
            continue
        lead = _optional_float(row.get("capture_lead_minutes"))
        if lead is None:
            exclusions["capture_lead_missing"] += 1
        elif lead < minimum_lead_minutes:
            exclusions["too_close_or_after_start"] += 1
        elif lead > maximum_lead_minutes:
            exclusions["captured_too_early"] += 1
        else:
            primary_candidates.append(row)

    primary_rows = _select_primary_rows(primary_candidates)
    primary_metrics = performance_metrics(primary_rows)
    evidence_gate = _evidence_gate(primary_metrics)
    model_breakdown = _model_breakdown(primary_rows)
    by_slate = _grouped_metrics(primary_rows, "screen_date")
    by_game = _grouped_metrics(primary_rows, "game_id")
    resolution_totals = {
        "graded_count": sum(len(report.get("graded", [])) for report in reports),
        "void_dnp_count": sum(len(report.get("voided", [])) for report in reports),
        "unresolved_count": sum(len(report.get("unresolved", [])) for report in reports),
        "pending_count": sum(len(report.get("pending", [])) for report in reports),
        "boxscore_fetch_error_count": sum(
            len(report.get("boxscore_fetch_errors", [])) for report in reports
        ),
    }

    return {
        "mode": "shadow_projection_rollup",
        "research_only": True,
        "capture_window": {
            "minimum_lead_minutes": minimum_lead_minutes,
            "maximum_lead_minutes": maximum_lead_minutes,
        },
        "source_report_count": len(reports),
        "resolution_totals": resolution_totals,
        "all_resolved_diagnostics": performance_metrics(all_rows),
        "primary_pregame": primary_metrics,
        "primary_projection_ids": [row.get("projection_id") for row in primary_rows],
        "excluded_from_primary": {
            "count": sum(exclusions.values()),
            "reasons": dict(sorted(exclusions.items())),
        },
        "duplicate_primary_candidates_removed": len(primary_candidates) - len(primary_rows),
        "over_probability_calibration": calibration_buckets(primary_rows),
        "by_slate": by_slate,
        "by_game": by_game,
        "model_breakdown": model_breakdown,
        "evidence_gate": evidence_gate,
    }


def performance_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if row.get("model_side") in {"OVER", "UNDER"}]
    decided = [row for row in selected if row.get("selection_outcome") != "push"]
    priced = [row for row in selected if row.get("profit_units") is not None]
    brier_rows = [row for row in rows if row.get("over_brier_score") is not None]
    market_briers = [value for row in rows if (value := _market_brier(row)) is not None]
    interval_rows = [
        row
        for row in rows
        if row.get("percentile_10") is not None and row.get("percentile_90") is not None
    ]
    both_priced = [
        row
        for row in rows
        if is_valid_price(row.get("over_odds")) and is_valid_price(row.get("under_odds"))
    ]
    wins = sum(1 for row in selected if row.get("selection_outcome") == "win")
    losses = sum(1 for row in selected if row.get("selection_outcome") == "loss")
    pushes = sum(1 for row in selected if row.get("selection_outcome") == "push")
    total_units = sum(float(row["profit_units"]) for row in priced)
    model_mae = _rounded_mean([float(row["absolute_error_points"]) for row in rows])
    line_errors = [abs(float(row["actual_points"]) - float(row["line"])) for row in rows]
    line_mae = _rounded_mean(line_errors)

    return {
        "projection_count": len(rows),
        "slate_count": len({str(row.get("screen_date", "")) for row in rows}),
        "game_count": len({str(row.get("game_id", "")) for row in rows}),
        "model_versions": sorted({str(row.get("model_version", "")) for row in rows}),
        "model_identity_count": len(
            {
                (
                    str(row.get("model_version", "")),
                    str(row.get("model_config_hash", "")),
                    str(row.get("code_commit", "")),
                )
                for row in rows
            }
        ),
        "both_side_price_count": len(both_priced),
        "both_side_price_rate": round(len(both_priced) / len(rows), 4) if rows else None,
        "spread_context_count": sum(row.get("team_spread") is not None for row in rows),
        "game_total_context_count": sum(row.get("game_total") is not None for row in rows),
        "mean_absolute_error_points": model_mae,
        "root_mean_squared_error_points": _rounded_rmse(
            [float(row["projection_error_points"]) for row in rows]
        ),
        "mean_projection_bias_points": _rounded_mean(
            [float(row["projection_error_points"]) for row in rows]
        ),
        "sportsbook_line_mean_absolute_error": line_mae,
        "model_mae_minus_line_mae": (
            round(model_mae - line_mae, 4)
            if model_mae is not None and line_mae is not None
            else None
        ),
        "model_lower_absolute_error_count": sum(
            float(row["absolute_error_points"])
            < abs(float(row["actual_points"]) - float(row["line"]))
            for row in rows
        ),
        "mean_absolute_error_minutes": _rounded_mean(
            [float(row["absolute_error_minutes"]) for row in rows]
        ),
        "mean_projection_bias_minutes": _rounded_mean(
            [float(row["projection_error_minutes"]) for row in rows]
        ),
        "model_over_brier_score": _rounded_mean(
            [float(row["over_brier_score"]) for row in brier_rows]
        ),
        "market_no_vig_over_brier_score": _rounded_mean(market_briers),
        "interval_10_90_count": len(interval_rows),
        "interval_10_90_coverage": (
            round(
                sum(
                    float(row["percentile_10"])
                    <= float(row["actual_points"])
                    <= float(row["percentile_90"])
                    for row in interval_rows
                )
                / len(interval_rows),
                4,
            )
            if interval_rows
            else None
        ),
        "selected_count": len(selected),
        "selected_wins": wins,
        "selected_losses": losses,
        "selected_pushes": pushes,
        "selected_hit_rate": round(wins / len(decided), 4) if decided else None,
        "priced_selected_count": len(priced),
        "flat_stake_units": round(total_units, 4) if priced else None,
        "flat_stake_roi": round(total_units / len(priced), 4) if priced else None,
    }


def render_shadow_rollup(rollup: dict[str, Any]) -> str:
    primary = rollup["primary_pregame"]
    all_rows = rollup["all_resolved_diagnostics"]
    gate = rollup["evidence_gate"]
    lines = [
        "WNBA PTS Shadow Multi-Slate Rollup (research only)",
        f"Final snapshot reports: {rollup['source_report_count']}",
        f"All resolved projections: {all_rows['projection_count']}",
        f"Strict pregame projections: {primary['projection_count']}",
        f"Excluded from primary evidence: {rollup['excluded_from_primary']['count']}",
        f"Evidence gate: {gate['status']}",
        (
            "Progress: "
            f"{primary['slate_count']}/{EVIDENCE_TARGETS['minimum_slates']} slates, "
            f"{primary['game_count']}/{EVIDENCE_TARGETS['minimum_games']} games, "
            f"{primary['projection_count']}/{EVIDENCE_TARGETS['minimum_projections']} projections"
        ),
    ]
    if primary["projection_count"]:
        lines.extend(
            [
                f"Model points MAE: {_display(primary['mean_absolute_error_points'])}",
                f"Sportsbook-line MAE: {_display(primary['sportsbook_line_mean_absolute_error'])}",
                f"Model over Brier: {_display(primary['model_over_brier_score'])}",
                f"Market no-vig over Brier: {_display(primary['market_no_vig_over_brier_score'])}",
                f"10th-90th interval coverage: {_display_percent(primary['interval_10_90_coverage'])}",
                f"Both-side price coverage: {_display_percent(primary['both_side_price_rate'])}",
                f"Research selections: {primary['selected_count']}",
                f"Flat-stake units: {_display_signed(primary['flat_stake_units'])}",
                f"Flat-stake ROI: {_display_percent(primary['flat_stake_roi'])}",
            ]
        )
    else:
        lines.append("No completed projection currently meets the strict pregame window.")
    lines.extend(
        [
            "",
            "The evidence gate measures collection sufficiency, not proof of betting edge.",
            "Model changes should be evaluated only on future, untouched snapshots.",
        ]
    )
    return "\n".join(lines)


def _select_primary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("model_version", "")),
            str(row.get("model_config_hash", "")),
            str(row.get("code_commit", "")),
            str(row.get("game_id", "")),
            str(row.get("player_name_norm", "")),
            str(row.get("prop_type", "")),
            str(row.get("bookmaker", "")),
        )
        grouped[key].append(row)
    selected = [
        min(
            group_rows,
            key=lambda row: (
                float(row["capture_lead_minutes"]),
                str(row.get("projection_id", "")),
            ),
        )
        for group_rows in grouped.values()
    ]
    return sorted(
        selected,
        key=lambda row: (
            str(row.get("screen_date", "")),
            str(row.get("game_id", "")),
            str(row.get("player_name_norm", "")),
        ),
    )


def _grouped_metrics(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field, ""))].append(row)
    return [
        {field: key, **performance_metrics(group_rows)}
        for key, group_rows in sorted(grouped.items())
    ]


def _model_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row.get("model_version", "")),
                str(row.get("model_config_hash", "")),
                str(row.get("code_commit", "")),
            )
        ].append(row)
    breakdown = []
    for (version, config_hash, code_commit), group_rows in sorted(grouped.items()):
        metrics = performance_metrics(group_rows)
        breakdown.append(
            {
                "model_version": version,
                "model_config_hash": config_hash,
                "code_commit": code_commit,
                "metrics": metrics,
                "evidence_gate": _evidence_gate(metrics),
            }
        )
    return breakdown


def _evidence_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "minimum_slates": metrics["slate_count"] >= EVIDENCE_TARGETS["minimum_slates"],
        "minimum_games": metrics["game_count"] >= EVIDENCE_TARGETS["minimum_games"],
        "minimum_projections": (
            metrics["projection_count"] >= EVIDENCE_TARGETS["minimum_projections"]
        ),
        "minimum_both_side_price_rate": (
            metrics["both_side_price_rate"] is not None
            and metrics["both_side_price_rate"]
            >= EVIDENCE_TARGETS["minimum_both_side_price_rate"]
        ),
    }
    mixed_models = metrics["model_identity_count"] > 1
    if mixed_models:
        status = "MIXED_MODELS"
    elif all(checks.values()):
        status = "READY_FOR_REVIEW"
    else:
        status = "COLLECTING"
    return {
        "status": status,
        "checks": checks,
        "targets": EVIDENCE_TARGETS,
        "mixed_models": mixed_models,
    }


def _market_brier(row: dict[str, Any]) -> float | None:
    if row.get("actual_over") is None:
        return None
    over = implied_probability(_optional_int(row.get("over_odds")))
    under = implied_probability(_optional_int(row.get("under_odds")))
    if over is None or under is None or over + under <= 0.0:
        return None
    no_vig_over = over / (over + under)
    actual = 1.0 if row["actual_over"] else 0.0
    return (no_vig_over - actual) ** 2


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _audit_status(row: dict[str, Any]) -> str | None:
    dirty = row.get("code_dirty")
    commit = row.get("code_commit")
    if dirty is True:
        return "code_dirty"
    if commit is None or not str(commit).strip():
        return "code_commit_missing"
    if dirty is None:
        return "code_state_missing"
    return None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _rounded_rmse(values: list[float]) -> float | None:
    return round(math.sqrt(mean(value * value for value in values)), 4) if values else None


def _display(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _display_signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.4f}"


def _display_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"
