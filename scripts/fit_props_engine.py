"""Fit per-prop projection artifacts (minutes/rate residuals + calibration).

Reads the Basketball-Reference parsed gamelog cache, builds point-in-time
player features, and produces one frozen artifact per prop type containing
joint (minutes_z, rate_error) residual pairs plus a probability shrink factor.

Usage:
    python3 scripts/fit_props_engine.py [--log-dir outputs/hunt/windows_cache/shared]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.features.player import (  # noqa: E402
    build_player_feature_table,
    stat_value,
)
from wnba_props.models import PlayerGameLog  # noqa: E402
from wnba_props.modeling.calibration import (  # noqa: E402
    fit_calibration_lambda,
    save_residual_artifact,
)
from wnba_props.modeling.registry import code_commit  # noqa: E402
from wnba_props.modeling.minutes import project_minutes  # noqa: E402
from wnba_props.modeling.rates import project_rate  # noqa: E402

DEFAULT_LOG_DIR = REPO_ROOT / "outputs" / "hunt" / "windows_cache" / "shared"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "wnba_props" / "artifacts"
PROP_TYPES = ("PTS", "REB", "AST", "3PM")
MODEL_ID = "wnba-props-residual-v1"
SOURCE_MODEL_VERSION = "wnba-props-shadow-v2"
CALIBRATION_SIMULATIONS = 400


def load_logs(log_dir: Path) -> list[PlayerGameLog]:
    logs: list[PlayerGameLog] = []
    for path in sorted(log_dir.glob("bref_parsed_gamelog_*_2026_v2.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            continue
        for row in data:
            if not row.get("did_play"):
                continue
            try:
                logs.append(
                    PlayerGameLog(
                        player_name_raw=str(row["player_name_raw"]),
                        player_name_norm=str(row["player_name_norm"]),
                        game_date=date.fromisoformat(str(row["game_date"])),
                        team=str(row["team"]),
                        opponent=str(row["opponent"]),
                        minutes=float(row["minutes"]),
                        points=int(row["points"]),
                        rebounds=int(row["rebounds"]),
                        assists=int(row["assists"]),
                        threes_made=int(row["threes_made"]),
                        did_play=bool(row.get("did_play", True)),
                        source=str(row.get("source", "basketball_reference")),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
    logs.sort(key=lambda log: log.game_date)
    return logs


def _actual_lookup(logs: list[PlayerGameLog]) -> dict[tuple[str, date], PlayerGameLog]:
    lookup: dict[tuple[str, date], PlayerGameLog] = {}
    for log in logs:
        lookup.setdefault((log.player_name_norm, log.game_date), log)
    return lookup


def build_prop_observations(features_rows, actual_lookup):
    """Attach actual outcomes and residual pairs to each feature row."""
    observations = []
    for features in features_rows:
        actual = actual_lookup.get((features.player_name_norm, features.game_date))
        if actual is None or actual.minutes <= 1.0:
            continue
        minutes = project_minutes(features)
        if minutes.minutes_sd <= 0.0:
            continue
        projected_rate = project_rate(features).projected_rate
        actual_stat = stat_value(actual, features.prop_type)
        actual_rate = actual_stat / actual.minutes
        minutes_z = (actual.minutes - minutes.projected_minutes) / minutes.minutes_sd
        rate_error = actual_rate - projected_rate
        observations.append(
            {
                "object": features,
                "minutes": minutes,
                "projected_rate": projected_rate,
                "actual_stat": actual_stat,
                "actual_minutes": actual.minutes,
                "minutes_z": minutes_z,
                "rate_error": rate_error,
            }
        )
    return observations


def fit_prop_artifact(prop_type: str, observations, artifact_dir: Path):
    if not observations:
        return None
    observations.sort(key=lambda item: item["object"].game_date)
    split = int(len(observations) * 0.7)
    fit_observations = observations[:split]
    calibration_observations = observations[split:] or observations

    pairs = [(o["minutes_z"], o["rate_error"]) for o in fit_observations]

    raw_pairs = []
    for observation in calibration_observations:
        features = observation["object"]
        line = round(features.rate_l5 * features.minutes_avg_l5)
        if line <= 0:
            continue
        probability = _simulate_over_probability(observation, pairs, line)
        if probability is None:
            continue
        raw_pairs.append((probability, 1 if observation["actual_stat"] > line else 0))

    calibration_lambda = fit_calibration_lambda(raw_pairs) if raw_pairs else 1.0

    artifact_path = artifact_dir / f"{prop_type.lower()}_engine_artifact.json"
    save_residual_artifact(
        artifact_path,
        model_id=MODEL_ID,
        prop_type=prop_type,
        source_model_version=SOURCE_MODEL_VERSION,
        calibration_lambda=calibration_lambda,
        pairs=pairs,
        trained_through=observations[-1]["object"].game_date.isoformat(),
        code_commit=code_commit(),
    )

    return {
        "prop_type": prop_type,
        "observations": len(observations),
        "fit_pairs": len(pairs),
        "calibration_rows": len(raw_pairs),
        "calibration_lambda": round(calibration_lambda, 4),
        "artifact": str(artifact_path.relative_to(REPO_ROOT)),
    }


def _simulate_over_probability(observation, pairs, line):
    import random

    if not pairs:
        return None
    rng = random.Random(hash((observation["object"].player_name_norm, line)))
    minutes = observation["minutes"]
    projected_rate = observation["projected_rate"]
    pair_count = len(pairs)
    wins = losses = 0
    for _ in range(CALIBRATION_SIMULATIONS):
        minutes_z, rate_error = pairs[rng.randrange(pair_count)]
        sampled_minutes = max(0.0, minutes.projected_minutes + minutes.minutes_sd * minutes_z)
        sampled_rate = max(0.0, projected_rate + rate_error)
        value = max(0, int(round(sampled_minutes * sampled_rate)))
        if value > line:
            wins += 1
        elif value < line:
            losses += 1
    non_push = wins + losses
    if non_push == 0:
        return None
    return wins / non_push


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    args = parser.parse_args()

    logs = load_logs(args.log_dir)
    if not logs:
        raise SystemExit(f"no logs found under {args.log_dir}")

    feature_rows = build_player_feature_table(logs=logs, prop_types=PROP_TYPES)
    actual_lookup = _actual_lookup(logs)

    reports = []
    for prop_type in PROP_TYPES:
        rows = [row for row in feature_rows if row.prop_type == prop_type]
        if not rows:
            continue
        observations = build_prop_observations(rows, actual_lookup)
        report = fit_prop_artifact(prop_type, observations, args.artifact_dir)
        if report is not None:
            reports.append(report)
            print(report)

    evidence_path = REPO_ROOT / "outputs" / "research" / "props_fit.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(reports, indent=2, sort_keys=True))
    print(f"wrote {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
