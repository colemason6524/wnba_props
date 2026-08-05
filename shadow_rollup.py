from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wnba_props.config import OUTPUTS_DIR
from wnba_props.shadow.collection import capture_lead_minutes, projection_id
from wnba_props.shadow.rollup import build_shadow_rollup, render_shadow_rollup


def main() -> int:
    args = _parse_args()
    try:
        reports = _load_latest_final_reports(OUTPUTS_DIR / "backtests")
        if not reports:
            print("No terminal shadow grading reports are available.")
            return 0
        enriched = [_enrich_legacy_report(report) for report in reports]
        rollup = build_shadow_rollup(
            enriched,
            minimum_lead_minutes=args.capture_min_lead_minutes,
            maximum_lead_minutes=args.capture_max_lead_minutes,
        )
        rollup["generated_at"] = datetime.now(timezone.utc).isoformat()
        json_path, text_path = _write_rollup(rollup)
        print(render_shadow_rollup(rollup))
        print(f"JSON rollup: {json_path}")
        print(f"Text rollup: {text_path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Shadow rollup failed: {exc}", file=sys.stderr)
        return 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate terminal WNBA PTS shadow grades without tuning the model."
    )
    parser.add_argument("--capture-min-lead-minutes", type=float, default=20.0)
    parser.add_argument("--capture-max-lead-minutes", type=float, default=90.0)
    return parser.parse_args()


def _load_latest_final_reports(output_dir: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted(output_dir.glob("shadow_grade_*.json")):
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("mode") != "shadow_projection_grade" or not _is_final(report):
            continue
        source = str(report.get("source_snapshot", ""))
        if not source:
            continue
        previous = latest.get(source)
        if previous is None or str(report.get("generated_at", "")) > str(
            previous.get("generated_at", "")
        ):
            latest[source] = report
    return [latest[key] for key in sorted(latest)]


def _is_final(report: dict[str, Any]) -> bool:
    return (
        not report.get("boxscore_fetch_errors")
        and int(report.get("summary", {}).get("pending_count", 0)) == 0
        and not any(
            item.get("reason") in {"game_status_missing", "final_boxscore_unavailable"}
            for item in report.get("unresolved", [])
        )
    )


def _enrich_legacy_report(report: dict[str, Any]) -> dict[str, Any]:
    snapshot_path = Path(str(report.get("source_snapshot", "")))
    if not snapshot_path.is_file():
        return report
    snapshot = json.loads(snapshot_path.read_text())
    projections = {
        (
            str(item.get("game_id", "")),
            str(item.get("player_name_norm", "")),
            float(item.get("line", 0.0)),
        ): item
        for item in snapshot.get("projections", [])
    }
    enriched_rows = []
    for original in report.get("graded", []):
        row = dict(original)
        projection = projections.get(
            (
                str(row.get("game_id", "")),
                str(row.get("player_name_norm", "")),
                float(row.get("line", 0.0)),
            )
        )
        if projection is not None:
            for field in (
                "game_time",
                "percentile_10",
                "percentile_90",
                "team_spread",
                "game_total",
                "flags",
                "price_status",
            ):
                row.setdefault(field, projection.get(field))
            row.setdefault("projection_id", projection.get("projection_id") or projection_id(projection))
            row.setdefault("capture_lead_minutes", _capture_lead(projection))
            row.setdefault(
                "captured_before_scheduled_start",
                row.get("capture_lead_minutes") is not None
                and float(row["capture_lead_minutes"]) > 0.0,
            )
        enriched_rows.append(row)
    enriched = dict(report)
    enriched["graded"] = enriched_rows
    return enriched


def _capture_lead(projection: dict[str, Any]) -> float | None:
    raw = projection.get("capture_lead_minutes")
    if raw is not None:
        return round(float(raw), 2)
    if not projection.get("game_time") or not projection.get("line_collected_at"):
        return None
    return capture_lead_minutes(
        datetime.fromisoformat(str(projection["game_time"])),
        datetime.fromisoformat(str(projection["line_collected_at"])),
    )


def _write_rollup(rollup: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = OUTPUTS_DIR / "backtests"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"shadow_rollup_{timestamp}.json"
    text_path = output_dir / f"shadow_rollup_{timestamp}.txt"
    json_path.write_text(json.dumps(rollup, indent=2, sort_keys=True))
    text_path.write_text(render_shadow_rollup(rollup) + "\n")
    return json_path, text_path


if __name__ == "__main__":
    raise SystemExit(main())
