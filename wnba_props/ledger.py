from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


PENDING = "PENDING"
WIN = "WIN"
LOSS = "LOSS"
PUSH = "PUSH"
VOID = "VOID"
UNPRICED = "UNPRICED"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _write_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def ledger_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("run_id", "")), str(row.get("proposition_id", ""))


def ledger_identity(row: dict[str, Any]) -> tuple[str, ...]:
    """Canonical identity used to supersede earlier snapshots.

    One row per game_date/market/subject: the most recent board wins. Falls
    back to proposition_id for rows missing the identity fields (legacy/tests).
    """
    game_date = row.get("game_date")
    market = row.get("market")
    subject = row.get("subject")
    if game_date and market and subject:
        return ("board", str(game_date), str(market), str(subject))
    return ("proposition", str(row.get("proposition_id", "")))


def is_settled(row: dict[str, Any]) -> bool:
    return bool(row.get("graded")) or row.get("outcome") not in {PENDING, UNPRICED}


def append_rows(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Append rows idempotently, letting newer snapshots supersede pending ones.

    Exact ``(run_id, proposition_id)`` duplicates are ignored. A row whose
    identity matches an earlier still-pending row replaces it, so re-running a
    later slot refreshes the line instead of double-counting the same play.
    Once a row is settled it is frozen and no duplicate is recorded.
    """
    existing = _read_rows(path)
    seen = {ledger_key(row) for row in existing}
    identity_index: dict[tuple[str, ...], int] = {}
    for index, row in enumerate(existing):
        identity_index[ledger_identity(row)] = index
    added = 0
    for row in rows:
        key = ledger_key(row)
        if key in seen:
            continue
        identity = ledger_identity(row)
        previous = identity_index.get(identity)
        if previous is not None:
            if is_settled(existing[previous]):
                continue
            existing[previous] = row
            identity_index[identity] = previous
            seen.add(key)
            added += 1
            continue
        existing.append(row)
        identity_index[identity] = len(existing) - 1
        seen.add(key)
        added += 1
    if added:
        _write_rows(path, existing)
    return added


def load_rows(path: Path) -> list[dict[str, Any]]:
    return _read_rows(path)


def settle_rows(
    path: Path,
    results: dict[str, tuple[str, Optional[float]]],
) -> int:
    """Settle a ledger by proposition_id.

    ``results`` maps proposition_id to ``(outcome, units)``. Only rows still
    PENDING are updated, which keeps the ledger idempotent.
    """
    rows = _read_rows(path)
    updated = 0
    timestamp = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if row.get("outcome") != PENDING:
            continue
        proposition_id = str(row.get("proposition_id", ""))
        if proposition_id not in results:
            continue
        outcome, units = results[proposition_id]
        row["outcome"] = outcome
        row["units"] = units
        row["graded"] = True
        row["graded_at"] = timestamp
        updated += 1
    if updated:
        _write_rows(path, rows)
    return updated


def roi_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    settled = [
        row
        for row in rows
        if row.get("units") is not None
        and row.get("outcome") in {WIN, LOSS, PUSH}
    ]
    units = sum(float(row.get("units") or 0.0) for row in settled)
    wins = sum(1 for row in settled if row["outcome"] == WIN)
    losses = sum(1 for row in settled if row["outcome"] == LOSS)
    pushes = sum(1 for row in settled if row["outcome"] == PUSH)
    return {
        "plays": len(settled),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "units": round(units, 3),
        "roi": round(units / len(settled), 4) if settled else None,
    }
