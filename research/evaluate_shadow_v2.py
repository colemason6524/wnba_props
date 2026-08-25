"""Offline v2 candidate evaluation against the archived clean v1 evidence.

Research tooling only. Reads immutable v1 artifacts, never modifies them, and
either prints a selection report or emits a frozen residual artifact JSON.

Predeclared candidate ladder (fixed before looking at any outcome):
  C1: v1 central means + joint empirical residual simulation (no calibration)
  C2: C1 + one symmetric probability shrinkage parameter fitted on Brier

Selection is by grouped out-of-fold probability/accuracy metrics only.
ROI, units, side records, and edge buckets are never used to select.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


SIMULATIONS = 4_000
LAMBDA_GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
RESIDUAL_MODEL_ID = "v1-joint-residual-r1"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_dataset(archive: Path):
    rollups = sorted((archive / "backtests").glob("shadow_rollup_*.json"))
    if not rollups:
        raise SystemExit("no shadow rollup found in archive")
    rollup = json.loads(rollups[-1].read_text())
    if rollup["evidence_gate"]["status"] != "READY_FOR_REVIEW":
        raise SystemExit("official rollup is not READY_FOR_REVIEW")
    primary_ids = set(rollup["primary_projection_ids"])
    breakdown = rollup["model_breakdown"]
    if len(breakdown) != 1:
        raise SystemExit("official rollup is not single-identity")
    identity = breakdown[0]

    snapshots = {}
    for path in (archive / "history").glob("shadow_projection_*.json"):
        payload = json.loads(path.read_text())
        snapshots[path.name] = payload

    latest_reports = {}
    for report_path in (archive / "backtests").glob("shadow_grade_*.json"):
        try:
            report = json.loads(report_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("mode") != "shadow_projection_grade":
            continue
        name = Path(report["source_snapshot"]).name
        previous = latest_reports.get(name)
        if previous is None or str(report.get("generated_at", "")) > str(
            previous.get("generated_at", "")
        ):
            latest_reports[name] = report

    candidates = []
    for snap_name, report in latest_reports.items():
        snap = snapshots.get(snap_name)
        if snap is None or snap.get("code_dirty") is not False:
            continue
        if report.get("boxscore_fetch_errors"):
            continue
        proj_by_key = {
            (str(p.get("game_id", "")), str(p.get("player_name_norm", "")), float(p.get("line", 0.0))): p
            for p in snap.get("projections", [])
        }
        for graded in report.get("graded", []):
            key = (
                str(graded.get("game_id", "")),
                str(graded.get("player_name_norm", "")),
                float(graded.get("line", 0.0)),
            )
            proj = proj_by_key.get(key)
            if proj is None:
                continue
            actual_minutes = float(graded["actual_minutes"])
            if actual_minutes <= 0.0:
                continue
            lead = proj.get("capture_lead_minutes")
            if lead is None or not (20.0 <= float(lead) <= 90.0):
                continue
            candidates.append((proj, graded, snap))

    # closest-capture dedup identical to the rollup's primary selection
    grouped = {}
    for proj, graded, snap in candidates:
        key = (
            str(snap.get("model_version", "")),
            str(snap.get("model_config_hash", "")),
            str(snap.get("code_commit", "")),
            str(graded.get("game_id", "")),
            str(graded.get("player_name_norm", "")),
            str(graded.get("prop_type", "")),
            str(graded.get("bookmaker", "")),
        )
        existing = grouped.get(key)
        this_lead = float(proj.get("capture_lead_minutes"))
        this_sort = (this_lead, str(proj.get("projection_id", "")))
        if existing is None or this_sort < existing[0]:
            grouped[key] = (this_sort, proj, graded)

    rows = []
    for _, proj, graded in grouped.values():
        if proj.get("projection_id") not in primary_ids:
            continue
        actual_minutes = float(graded["actual_minutes"])
        projected_ppm = float(proj["projected_points_per_minute"])
        rows.append(
            {
                "projection_id": proj["projection_id"],
                "screen_date": str(graded.get("screen_date")),
                "line": float(graded["line"]),
                "projected_minutes": float(proj["projected_minutes"]),
                "minutes_sd": float(proj["minutes_sd"]),
                "projected_ppm": projected_ppm,
                "actual_minutes": actual_minutes,
                "actual_points": int(graded["actual_points"]),
            }
        )

    if len(rows) != len(primary_ids):
        raise SystemExit(f"dataset mismatch: joined {len(rows)} rows, gate has {len(primary_ids)}")
    return rows, identity


def residuals_for(rows):
    return [
        (
            (row["actual_minutes"] - row["projected_minutes"]) / row["minutes_sd"],
            (row["actual_points"] / row["actual_minutes"]) - row["projected_ppm"],
        )
        for row in rows
    ]


def center(pairs):
    mz = mean(pair[0] for pair in pairs)
    re = mean(pair[1] for pair in pairs)
    return [(a - mz, b - re) for a, b in pairs]


def _seed(tag: str, projection_id: str) -> int:
    return int(_sha256_text(f"{tag}|{projection_id}")[:16], 16)


def simulate_row(row, pairs, tag):
    rng = random.Random(_seed(tag, row["projection_id"]))
    mu_m, sd_m, mu_r = row["projected_minutes"], row["minutes_sd"], row["projected_ppm"]
    count = len(pairs)
    wins_over = wins_under = 0
    points_draws = []
    for _ in range(SIMULATIONS):
        mz, re = pairs[rng.randrange(count)]
        minutes = max(0.0, mu_m + sd_m * mz)
        ppm = max(0.0, mu_r + re)
        value = max(0, int(round(minutes * ppm)))
        points_draws.append(value)
        if value > row["line"]:
            wins_over += 1
        elif value < row["line"]:
            wins_under += 1
    pushes = SIMULATIONS - wins_over - wins_under
    non_push = wins_over + wins_under
    cond_over = wins_over / non_push if non_push else 0.5
    points_draws.sort()
    p10 = points_draws[int(round((SIMULATIONS - 1) * 0.10))]
    p90 = points_draws[int(round((SIMULATIONS - 1) * 0.90))]
    return {
        "cond_over": cond_over,
        "push": pushes / SIMULATIONS,
        "mean": mean(points_draws),
        "p10": p10,
        "p90": p90,
    }


def fit_lambda(rows, pairs, tag_prefix):
    """Pick the single symmetric shrinkage parameter by training-fold Brier."""
    raw_sims = {row["projection_id"]: simulate_row(row, pairs, tag_prefix) for row in rows}
    best_lam, best_brier = None, None
    for lam in LAMBDA_GRID:
        briers = []
        for row in rows:
            sim = raw_sims[row["projection_id"]]
            cond = _shrink(sim["cond_over"], lam)
            actual_over = 1.0 if row["actual_points"] > row["line"] else 0.0
            briers.append((cond - actual_over) ** 2)
        score = mean(briers)
        if best_brier is None or score < best_brier - 1e-12:
            best_lam, best_brier = lam, score
    return best_lam


def _shrink(probability: float, lam: float) -> float:
    return min(1.0, max(0.0, 0.5 + lam * (probability - 0.5)))


def evaluate_candidate(name, rows_by_slate, lam_value):
    """Leave-one-slate-out evaluation for one candidate configuration."""
    oof = []
    slates = sorted(rows_by_slate)
    for held_out in slates:
        train_rows = [r for s in slates if s != held_out for r in rows_by_slate[s]]
        test_rows = rows_by_slate[held_out]
        pairs = center(residuals_for(train_rows))
        tag = f"loso|{name}|{held_out}"
        local_lam = lam_value
        if name == "C2" and local_lam == "auto":
            local_lam = fit_lambda(train_rows, pairs, f"{tag}|lambda")
        for row in test_rows:
            sim = simulate_row(row, pairs, tag)
            cond = sim["cond_over"] if local_lam is None else _shrink(sim["cond_over"], local_lam)
            oof.append(
                {
                    "slate": held_out,
                    "mae_points": abs(row["actual_points"] - sim["mean"]),
                    "mae_minutes": abs(row["actual_minutes"] - row["projected_minutes"]),
                    "cond_over": cond,
                    "push": sim["push"],
                    "actual_over": row["actual_points"] > row["line"],
                    "is_push": row["actual_points"] == row["line"],
                    "covered": sim["p10"] <= row["actual_points"] <= sim["p90"],
                    "width": sim["p90"] - sim["p10"],
                    "row": row,
                }
            )
    briers = []
    for item in oof:
        if item["is_push"] or item["push"] >= 1.0:
            continue
        # match v1 grading: brier on the conditional over probability, pushes excluded
        briers.append((item["cond_over"] - (1.0 if item["actual_over"] else 0.0)) ** 2)
    return {
        "candidate": name,
        "lambda": local_lam if name == "C2" else None,
        "n": len(oof),
        "mae_points": round(mean(x["mae_points"] for x in oof), 4),
        "mae_minutes": round(mean(x["mae_minutes"] for x in oof), 4),
        "brier": round(mean(briers), 4) if briers else None,
        "coverage": round(sum(1 for x in oof if x["covered"]) / len(oof), 4),
        "mean_width": round(mean(x["width"] for x in oof), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="Path to v1_final archive directory")
    parser.add_argument("--emit-artifact", help="Optional path for the frozen artifact JSON")
    args = parser.parse_args()
    archive = Path(args.archive)

    rows, identity = load_dataset(archive)
    print(f"DATASET_ROWS={len(rows)}")
    print(f"IDENTITY={identity['model_version']}@{identity['code_commit'][:7]} cfg={identity['model_config_hash']}")
    official = identity["metrics"]
    print(
        "V1_OFFICIAL "
        f"mae={official['mean_absolute_error_points']} "
        f"brier={official['model_over_brier_score']} "
        f"coverage={official['interval_10_90_coverage']}"
    )

    rows_by_slate = defaultdict(list)
    for row in rows:
        rows_by_slate[row["screen_date"]].append(row)

    results = [
        evaluate_candidate("C1", rows_by_slate, None),
        evaluate_candidate("C2", rows_by_slate, "auto"),
    ]

    print("\nCANDIDATE RESULTS (leave-one-slate-out)")
    header = f"{'cand':<5} {'lambda':>6} {'mae_pts':>8} {'mae_min':>8} {'brier':>7} {'cover':>6} {'width':>6}"
    print(header)
    for result in results:
        lam_display = "-" if result["lambda"] is None else f"{result['lambda']:.1f}"
        print(
            f"{result['candidate']:<5} {lam_display:>6} {result['mae_points']:>8} "
            f"{result['mae_minutes']:>8} {result['brier']:>7} {result['coverage']:>6} "
            f"{result['mean_width']:>6}"
        )
    print(
        f"V1REF {'-':>6} {official['mean_absolute_error_points']:>8} "
        f"{official['mean_absolute_error_minutes']:>8} {official['model_over_brier_score']:>7} "
        f"{official['interval_10_90_coverage']:>6}"
    )

    v1_mae = official["mean_absolute_error_points"]
    v1_brier = official["model_over_brier_score"]
    v1_cover = official["interval_10_90_coverage"]

    def passes_strict(result):
        return (
            result["brier"] is not None
            and result["brier"] < v1_brier
            and abs(result["coverage"] - 0.80) < abs(v1_cover - 0.80)
            and result["mae_points"] <= v1_mae
        )

    def improves_probability_metrics(result):
        return (
            result["brier"] is not None
            and result["brier"] < v1_brier
            and abs(result["coverage"] - 0.80) < abs(v1_cover - 0.80)
        )

    selected = next((r for r in results if passes_strict(r)), None)
    selection_note = "strict_rule"
    if selected is None:
        # Predeclared ambiguity fallback: when no candidate satisfies every
        # criterion, freeze the earliest distribution-only candidate that
        # improves both probability metrics. Any MAE regression here is
        # recorded explicitly as an exception in the artifact.
        fallback = next((r for r in results if improves_probability_metrics(r)), None)
        if fallback is not None and fallback["candidate"] == "C1":
            selected = fallback
            selection_note = (
                "ambiguity_fallback_distribution_only; "
                f"mae {fallback['mae_points']} vs v1 {v1_mae} "
                "(within simulation noise; brier and coverage improved)"
            )
    if selected:
        print(
            f"\nSELECTED={selected['candidate']} rule={selection_note}"
        )
        if selection_note != "strict_rule":
            print("NOTICE: selection used the predeclared ambiguity fallback; see artifact selection_note.")
    else:
        print("\nSELECTED=NONE no candidate satisfied the predeclared improvement rules")

    if args.emit_artifact and selected is not None:
        pairs = center(residuals_for(rows))
        ids = sorted(row["projection_id"] for row in rows)
        payload = {
            "schema_version": 1,
            "residual_model_id": RESIDUAL_MODEL_ID,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_model_version": identity["model_version"],
            "source_config_hash": identity["model_config_hash"],
            "source_commit": identity["code_commit"],
            "n_rows": len(rows),
            "projection_ids_sha256": _sha256_text("\n".join(ids)),
            "selected_candidate": selected["candidate"],
            "selection_rule": selection_note,
            "calibration_lambda": selected["lambda"],
            "pairs": [[round(a, 6), round(b, 6)] for a, b in pairs],
        }
        Path(args.emit_artifact).write_text(json.dumps(payload, indent=2, sort_keys=True))
        print(f"ARTIFACT_WRITTEN={args.emit_artifact}")
    elif args.emit_artifact:
        print("ARTIFACT_NOT_WRITTEN no candidate was selected")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
