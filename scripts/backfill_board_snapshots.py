"""Backfill immutable board snapshots into the canonical ledger.

Historical boards created before snapshot IDs existed cannot be re-derived from
the ledger alone. This script reads the JSON board snapshots, assigns each row a
snapshot ID derived from the board's run and creation time, and appends the
capture. ``append_rows`` keeps the old capture for audit and lets the newer
snapshot become the latest evaluation row.

Dry-run by default. Use ``--apply`` to write and ``--backup`` to copy the ledger
before the first write.

Usage:
    python3 scripts/backfill_board_snapshots.py [--apply] [--backup]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.ledger import append_rows, latest_rows, load_rows  # noqa: E402

DEFAULT_LEDGER = REPO_ROOT / "outputs" / "ledger" / "forecast_ledger.jsonl"
DEFAULT_BOARDS = REPO_ROOT / "outputs" / "forecast_boards"


def _snapshot_id(board: dict, path: Path) -> str:
    existing = str(board.get("snapshot_id") or "")
    if existing:
        return existing
    run_id = str(board.get("run_id") or path.stem)
    created = str(board.get("created_at") or "")
    if created:
        try:
            stamp = datetime.fromisoformat(created).astimezone(timezone.utc)
            suffix = stamp.strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            suffix = "unknown"
    else:
        suffix = path.stem.rsplit("_", 1)[-1]
    return f"{run_id}-{suffix}"


def build_rows(ledger_path: Path, boards_dir: Path) -> list[dict]:
    existing = load_rows(ledger_path)
    latest_ids = {
        (
            str(row.get("snapshot_id") or row.get("run_id") or ""),
            str(row.get("proposition_id") or ""),
        )
        for row in latest_rows(existing)
    }
    staged: list[dict] = []
    for board_path in sorted(boards_dir.glob("forecast_board_*.json")):
        try:
            board = json.loads(board_path.read_text())
        except json.JSONDecodeError:
            continue
        snapshot_id = _snapshot_id(board, board_path)
        phase = str(board.get("phase") or "regular").lower()
        for row in board.get("ledger_rows", []):
            new_row = dict(row)
            new_row.setdefault("run_id", board.get("run_id", ""))
            new_row["snapshot_id"] = snapshot_id
            new_row["phase"] = new_row.get("phase") or phase
            key = (snapshot_id, str(new_row.get("proposition_id") or ""))
            if key in latest_ids:
                continue
            staged.append(new_row)
    return staged


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill board snapshots into ledger.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--boards", type=Path, default=DEFAULT_BOARDS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.ledger.exists():
        print(f"[backfill] no ledger at {args.ledger}", file=sys.stderr)
        return 1
    staged = build_rows(args.ledger, args.boards)
    print(f"[backfill] staged {len(staged)} board rows from {args.boards}")
    if not args.apply:
        print("[backfill] dry run; pass --apply to write")
        return 0
    if args.backup:
        backup = args.ledger.with_suffix(
            args.ledger.suffix
            + f".backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(args.ledger, backup)
        print(f"[backfill] backup written to {backup}")
    added = append_rows(args.ledger, staged)
    print(f"[backfill] appended {added} rows to {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
