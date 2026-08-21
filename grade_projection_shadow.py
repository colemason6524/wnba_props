from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR
from wnba_props.shadow.grading import grade_shadow_projections, render_grading_report
from wnba_props.shadow.sources import ShadowEspnBoxscoreSource, ShadowEspnSlateSource


def main() -> int:
    args = _parse_args()
    try:
        snapshot_paths = _resolve_snapshot_paths(args.snapshot, all_pending=args.all_pending)
        if not snapshot_paths:
            print("No pending shadow snapshots require grading.")
            return 0
        written = _grade_snapshots(snapshot_paths)
        for index, (report, json_path, text_path) in enumerate(written):
            if index:
                print("")
            print(render_grading_report(report))
            print(f"JSON report: {json_path}")
            print(f"Text report: {text_path}")
        print("")
        print(f"Shadow grading batch: {len(written)} snapshot(s) processed.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Shadow grading failed: {exc}", file=sys.stderr)
        return 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade a research-only WNBA PTS shadow snapshot after games become final."
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--snapshot",
        help="Path to a shadow_projection_*.json file; defaults to the newest snapshot.",
    )
    input_group.add_argument(
        "--all-pending",
        action="store_true",
        help="Grade every snapshot without a terminal final-game report.",
    )
    return parser.parse_args()


def _resolve_snapshot_paths(raw_path: str | None, *, all_pending: bool) -> list[Path]:
    if raw_path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            raise FileNotFoundError(f"snapshot not found: {path}")
        return [path]

    candidates = sorted((OUTPUTS_DIR / "history").glob("shadow_projection_*.json"))
    if not candidates:
        if all_pending:
            return []
        raise FileNotFoundError("no shadow projection snapshots found in outputs/history")
    if all_pending:
        return _pending_snapshot_paths(
            candidates,
            sorted((OUTPUTS_DIR / "backtests").glob("shadow_grade_*.json")),
        )
    return [candidates[-1]]


def _pending_snapshot_paths(
    snapshot_paths: list[Path],
    report_paths: list[Path],
) -> list[Path]:
    latest_reports: dict[str, dict[str, Any]] = {}
    for report_path in report_paths:
        try:
            report = json.loads(report_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("mode") != "shadow_projection_grade":
            continue
        source = report.get("source_snapshot")
        if not source:
            continue
        source_key = str(Path(str(source)).resolve())
        previous = latest_reports.get(source_key)
        if previous is None or str(report.get("generated_at", "")) > str(
            previous.get("generated_at", "")
        ):
            latest_reports[source_key] = report

    pending = []
    for snapshot_path in snapshot_paths:
        report = latest_reports.get(str(snapshot_path.resolve()))
        if report is None or not _is_terminal_report(report):
            pending.append(snapshot_path)
    return pending


def _is_terminal_report(report: dict[str, Any]) -> bool:
    if report.get("boxscore_fetch_errors"):
        return False
    summary = report.get("summary", {})
    if int(summary.get("pending_count", 0)) > 0:
        return False
    retryable_reasons = {
        "game_status_missing",
        "final_boxscore_unavailable",
        "final_boxscore_player_missing",
    }
    return not any(
        item.get("reason") in retryable_reasons
        for item in report.get("unresolved", [])
    )


def _grade_snapshots(
    snapshot_paths: list[Path],
) -> list[tuple[dict[str, Any], Path, Path]]:
    inputs = [(path, _load_snapshot(path)) for path in snapshot_paths]
    statuses_by_date = {}
    for screen_date in sorted(
        {date.fromisoformat(str(snapshot["screen_date"])) for _, snapshot in inputs}
    ):
        slate_source = ShadowEspnSlateSource()
        slate_source.fetch_games(screen_date)
        statuses_by_date[screen_date] = slate_source.game_statuses

    all_final_game_ids = set()
    for _, snapshot in inputs:
        screen_date = date.fromisoformat(str(snapshot["screen_date"]))
        statuses = statuses_by_date[screen_date]
        all_final_game_ids.update(
            str(item.get("game_id", ""))
            for item in snapshot.get("projections", [])
            if statuses.get(str(item.get("game_id", ""))) is not None
            and statuses[str(item.get("game_id", ""))].completed
        )

    boxscore_source = ShadowEspnBoxscoreSource(
        JsonCache(CACHE_DIR / "shadow" / "grading", ttl_hours=6)
    )
    boxscores = {}
    boxscore_error_by_game = {}
    for game_id in sorted(all_final_game_ids):
        try:
            boxscores[game_id] = boxscore_source.fetch_boxscore(game_id)
        except Exception as exc:  # noqa: BLE001
            boxscore_error_by_game[game_id] = str(exc)

    written = []
    for snapshot_path, snapshot in inputs:
        screen_date = date.fromisoformat(str(snapshot["screen_date"]))
        projections = list(snapshot.get("projections", []))
        game_ids = {str(item.get("game_id", "")) for item in projections}
        grade = grade_shadow_projections(
            projections,
            game_statuses=statuses_by_date[screen_date],
            boxscores=boxscores,
        )
        report = {
            "mode": "shadow_projection_grade",
            "research_only": True,
            "metric_schema_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_snapshot": str(snapshot_path.resolve()),
            "source_snapshot_id": snapshot.get("snapshot_id"),
            "source_exported_at": snapshot.get("exported_at"),
            "source_capture_policy": snapshot.get("capture_policy"),
            "source_model_config_hash": snapshot.get("model_config_hash"),
            "source_code_commit": snapshot.get("code_commit"),
            "source_code_dirty": snapshot.get("code_dirty"),
            "screen_date": screen_date.isoformat(),
            "model_version": snapshot.get("model_version"),
            "boxscore_fetch_errors": [
                {"game_id": game_id, "error": error}
                for game_id, error in sorted(boxscore_error_by_game.items())
                if game_id in game_ids
            ],
            **grade,
        }
        json_path, text_path = _write_report(report)
        written.append((report, json_path, text_path))
    return written


def _load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if payload.get("mode") != "shadow_projection" or not payload.get("research_only"):
        raise ValueError("input is not a research-only shadow projection snapshot")
    if not payload.get("screen_date"):
        raise ValueError("snapshot is missing screen_date")
    if not isinstance(payload.get("projections"), list):
        raise ValueError("snapshot projections must be a list")
    return payload


def _write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = OUTPUTS_DIR / "backtests"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    source_token = Path(str(report["source_snapshot"])).stem.replace(
        "shadow_projection_", ""
    )
    stem = (
        f"shadow_grade_{report['screen_date'].replace('-', '')}_"
        f"{source_token}_{timestamp}"
    )
    json_path = output_dir / f"{stem}.json"
    text_path = output_dir / f"{stem}.txt"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    text_path.write_text(render_grading_report(report) + "\n")
    return json_path, text_path


if __name__ == "__main__":
    raise SystemExit(main())
