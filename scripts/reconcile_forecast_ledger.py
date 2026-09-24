"""Reconcile published forecast boards against the canonical ledger.

The ledger keeps every capture for audit. The official performance view uses the
latest capture per ``game_date/market/subject``. This script reports both views
and flags board rows whose current published values differ from the ledger row
that would otherwise answer for them, which is how the September preflight rows
were discovered.

Usage:
    python3 scripts/reconcile_forecast_ledger.py \
        [--ledger outputs/ledger/forecast_ledger.jsonl] \
        [--boards outputs/forecast_boards] \
        [--out outputs/research/regular_season_reconciliation.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.ledger import latest_rows, load_rows, roi_summary  # noqa: E402

DEFAULT_LEDGER = REPO_ROOT / "outputs" / "ledger" / "forecast_ledger.jsonl"
DEFAULT_BOARDS = REPO_ROOT / "outputs" / "forecast_boards"
DEFAULT_OUT = REPO_ROOT / "outputs" / "research" / "regular_season_reconciliation.json"

_COMPARE_FIELDS = ("price", "line", "projection", "probability", "pick", "value")


def identity(row: dict) -> tuple:
    return (
        str(row.get("game_date", "")),
        str(row.get("market", "")),
        str(row.get("subject", "")),
    )


def _latest_by_identity(rows: list[dict]) -> dict[tuple, dict]:
    return {identity(row): row for row in latest_rows(rows)}


def _summaries(rows: list[dict]) -> dict:
    by_phase: dict[str, list[dict]] = defaultdict(list)
    by_market: dict[str, list[dict]] = defaultdict(list)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in latest_rows(rows):
        phase = str(row.get("phase") or "regular").lower()
        by_phase[phase].append(row)
        by_market[str(row.get("market", ""))].append(row)
        by_date[str(row.get("game_date", ""))].append(row)
    return {
        "overall": roi_summary(rows),
        "by_phase": {key: roi_summary(items) for key, items in sorted(by_phase.items())},
        "by_market": {
            key: roi_summary(items) for key, items in sorted(by_market.items())
        },
        "by_date": {key: roi_summary(items) for key, items in sorted(by_date.items())},
    }


def reconcile(ledger_path: Path, boards_dir: Path) -> dict:
    ledger_rows = load_rows(ledger_path)
    latest = _latest_by_identity(ledger_rows)
    board_reports = []
    drifted = 0
    missing = 0
    matched = 0
    for board_path in sorted(boards_dir.glob("forecast_board_*.json")):
        try:
            board = json.loads(board_path.read_text())
        except json.JSONDecodeError:
            continue
        differences = []
        board_matched = 0
        board_missing = 0
        for row in board.get("ledger_rows", []):
            key = identity(row)
            current = latest.get(key)
            if current is None:
                board_missing += 1
                missing += 1
                continue
            changed = {
                field: {"board": row.get(field), "ledger": current.get(field)}
                for field in _COMPARE_FIELDS
                if row.get(field) != current.get(field)
            }
            if changed:
                drifted += 1
                differences.append(
                    {
                        "game_date": key[0],
                        "market": key[1],
                        "subject": key[2],
                        "changed": changed,
                        "board_snapshot_id": row.get("snapshot_id", ""),
                        "ledger_snapshot_id": current.get("snapshot_id", ""),
                    }
                )
            else:
                board_matched += 1
                matched += 1
        board_reports.append(
            {
                "board": board_path.name,
                "run_id": board.get("run_id", ""),
                "slot": board.get("slot", ""),
                "rows": len(board.get("ledger_rows", [])),
                "matched": board_matched,
                "drifted": len(differences),
                "missing": board_missing,
                "differences": differences[:50],
            }
        )
    return {
        "ledger_rows": len(ledger_rows),
        "latest_rows": len(latest),
        "summaries": _summaries(ledger_rows),
        "board_reports": board_reports,
        "board_totals": {
            "matched": matched,
            "drifted": drifted,
            "missing": missing,
            "boards": len(board_reports),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile forecast boards and ledger.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--boards", type=Path, default=DEFAULT_BOARDS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = reconcile(args.ledger, args.boards)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
    totals = report["board_totals"]
    print(
        f"[reconcile] ledger_rows={report['ledger_rows']} latest_rows={report['latest_rows']} "
        f"boards={totals['boards']} matched={totals['matched']} "
        f"drifted={totals['drifted']} missing={totals['missing']}"
    )
    print(f"[reconcile] wrote {args.out}")
    ready = report["summaries"]["overall"]
    print(
        f"[reconcile] latest ROI {ready['wins']}-{ready['losses']}-{ready['pushes']} "
        f"{ready['units']:+.3f}u"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
