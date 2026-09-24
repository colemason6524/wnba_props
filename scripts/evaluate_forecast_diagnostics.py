"""Calibration and ROI diagnostics from the canonical forecast ledger.

Uses the latest capture per identity (the agreed evaluation population) and
reports the diagnostics that explained the regular-season losses: selected-side
calibration, projection-versus-line buckets, and units by phase/market/side.

Usage:
    python3 scripts/evaluate_forecast_diagnostics.py \
        [--ledger outputs/ledger/forecast_ledger.jsonl] \
        [--out outputs/research/forecast_diagnostics.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.ledger import WIN, LOSS, PUSH, latest_rows, load_rows, roi_summary  # noqa: E402

DEFAULT_LEDGER = REPO_ROOT / "outputs" / "ledger" / "forecast_ledger.jsonl"
DEFAULT_OUT = REPO_ROOT / "outputs" / "research" / "forecast_diagnostics.json"

_PROB_BINS = ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 0.8), (0.8, 1.01))
_EDGE_BINS = ((0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1e9))


def _settled(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("outcome") in (WIN, LOSS, PUSH)]


def _rate(rows: list[dict]) -> dict:
    graded = [row for row in rows if row.get("outcome") in (WIN, LOSS)]
    if not graded:
        return {"n": 0}
    predicted = [
        float(row["probability"])
        for row in graded
        if row.get("probability") is not None
    ]
    actual = [1.0 if row["outcome"] == WIN else 0.0 for row in graded]
    brier = (
        sum(
            (float(row["probability"]) - (1.0 if row["outcome"] == WIN else 0.0)) ** 2
            for row in graded
            if row.get("probability") is not None
        )
        / len(predicted)
        if predicted
        else None
    )
    return {
        "n": len(graded),
        "avg_predicted": round(sum(predicted) / len(predicted), 4) if predicted else None,
        "hit_rate": round(sum(actual) / len(actual), 4),
        "brier": round(brier, 4) if brier is not None else None,
    }


def _calibration(rows: list[dict]) -> list[dict]:
    out = []
    for low, high in _PROB_BINS:
        bucket = [
            row
            for row in rows
            if row.get("outcome") in (WIN, LOSS)
            and row.get("probability") is not None
            and low <= float(row["probability"]) < high
        ]
        if bucket:
            out.append({"low": low, "high": high, **_rate(bucket)})
    return out


def _edge_buckets(rows: list[dict]) -> list[dict]:
    out = []
    for low, high in _EDGE_BINS:
        bucket = [
            row
            for row in rows
            if row.get("outcome") in (WIN, LOSS, PUSH)
            and row.get("projection") is not None
            and row.get("line") is not None
            and low <= abs(float(row["projection"]) - float(row["line"])) < high
        ]
        if bucket:
            out.append(
                {
                    "low": low,
                    "high": high,
                    "plays": len(bucket),
                    "units": round(sum(float(r.get("units") or 0.0) for r in bucket), 3),
                    "hit_rate": round(
                        sum(1 for r in bucket if r["outcome"] == WIN) / len(bucket), 4
                    ),
                }
            )
    return out


def build_report(ledger_path: Path) -> dict:
    rows = _settled(latest_rows(load_rows(ledger_path)))
    by_phase: dict[str, list[dict]] = defaultdict(list)
    by_market: dict[str, list[dict]] = defaultdict(list)
    by_side: dict[str, list[dict]] = defaultdict(list)
    by_value: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_phase[str(row.get("phase") or "regular").lower()].append(row)
        by_market[str(row.get("market", ""))].append(row)
        by_side[f"{row.get('market', '')}:{row.get('pick', '')}"].append(row)
        by_value[str(row.get("value", "unknown")).lower()].append(row)

    return {
        "overall": roi_summary(rows),
        "calibration": _calibration(rows),
        "edge_buckets": _edge_buckets(rows),
        "by_phase": {key: roi_summary(items) for key, items in sorted(by_phase.items())},
        "by_market": {
            key: {"roi": roi_summary(items), "calibration": _rate(items)}
            for key, items in sorted(by_market.items())
        },
        "by_side": {key: roi_summary(items) for key, items in sorted(by_side.items())},
        "by_value": {key: roi_summary(items) for key, items in sorted(by_value.items())},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forecast calibration diagnostics.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.ledger)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
    overall = report["overall"]
    print(
        f"[diagnostics] latest {overall['wins']}-{overall['losses']}-{overall['pushes']} "
        f"{overall['units']:+.3f}u roi={overall['roi']}"
    )
    print(f"[diagnostics] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
