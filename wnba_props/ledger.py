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
    """Identity for duplicate detection.

    ``snapshot_id`` distinguishes two captures of the same run (for example a
    preflight capture and the published board). Without it, a later capture
    would be treated as an exact duplicate of the earlier one and silently
    dropped.
    """
    snapshot = str(row.get("snapshot_id") or row.get("run_id", ""))
    return snapshot, str(row.get("proposition_id", ""))


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


def _capture_order(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("captured_at") or ""),
        str(row.get("snapshot_id") or row.get("run_id") or ""),
    )


def latest_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the newest capture for each canonical play identity.

    The ledger keeps every capture for audit. Settlement, grading, and ROI use
    only the latest capture so an earlier preflight snapshot can never stand in
    for the forecast that was actually published or intended.
    """
    best: dict[tuple[str, ...], tuple[tuple[str, str], dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in rows:
        if not (row.get("game_date") and row.get("market") and row.get("subject")):
            passthrough.append(row)
            continue
        identity = ledger_identity(row)
        order = _capture_order(row)
        previous = best.get(identity)
        if previous is None or order >= previous[0]:
            best[identity] = (order, row)
    return passthrough + [entry[1] for entry in best.values()]


def append_rows(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Append rows idempotently, letting newer snapshots supersede pending ones.

    Exact ``(snapshot_id, proposition_id)`` duplicates are ignored. A row whose
    identity matches an earlier still-pending row replaces it, so re-running a
    later slot refreshes the line instead of double-counting the same play.

    Once a row is settled it is frozen. A genuinely later capture of the same
    identity (identified by a later ``captured_at`` plus a different
    ``snapshot_id``) is appended as a new superseding row and the older row is
    annotated with ``superseded_by``. This preserves the audit trail without
    letting the older settlement answer for the newer forecast.
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
            previous_row = existing[previous]
            if is_settled(previous_row):
                incoming_captured = str(row.get("captured_at") or "")
                if incoming_captured and _capture_order(row) > _capture_order(previous_row):
                    previous_row["superseded_by"] = str(
                        row.get("snapshot_id") or row.get("run_id") or ""
                    )
                    existing.append(row)
                    identity_index[identity] = len(existing) - 1
                    seen.add(key)
                    added += 1
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
    latest_index: dict[tuple[str, ...], int] = {}
    for index, row in enumerate(rows):
        identity = ledger_identity(row)
        previous = latest_index.get(identity)
        if previous is None or _capture_order(row) >= _capture_order(rows[previous]):
            latest_index[identity] = index
    updated = 0
    timestamp = datetime.now(timezone.utc).isoformat()
    for index, row in enumerate(rows):
        if row.get("outcome") != PENDING:
            continue
        if latest_index.get(ledger_identity(row)) != index:
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
        for row in latest_rows(rows)
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
