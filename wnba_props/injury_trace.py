"""WNBA injury traceability: per-OUT-player event log (observability only).

Each forecast date records one JSONL event per redistributable OUT player
(status out / injured reserve / suspended), keyed by a stable event id of the
form ``{date}:{team}:{out_norm}:{status_norm}``. Events carry the vacated
minutes, the per-recipient attribution credits from
:func:`wnba_props.rotation.redistribute_out_minutes_explained`, and the team
total pre/post debit context.

This module never touches model logic: it only builds, appends and loads the
event log the pipeline writes and the adjudication/report tooling reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .rotation import normalize_status


def make_event_id(screen_date: str, team: str, out_norm: str, status: str) -> str:
    """Stable id: ``{date}:{team}:{out_norm}:{status_norm}`` (spaces kept)."""
    return f"{screen_date}:{team}:{out_norm}:{normalize_status(status)}"


def build_injury_events(
    *,
    screen_date: str,
    out_entries: Sequence[Mapping[str, object]],
    attribution: Mapping[str, Mapping[str, float]],
    vacated_minutes: Mapping[str, float] | None = None,
    team_debit_pts: Mapping[str, float] | None = None,
    team_total_pre_debit: Mapping[str, float] | None = None,
    team_total_post_debit: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build one traceable event per OUT entry.

    ``out_entries`` are the same mappings passed to the rotation
    redistribution (keys ``player_name_norm``, ``team``, ``status``,
    ``base_minutes``). ``attribution`` maps each OUT norm to its
    per-recipient credit mapping. Per-OUT ``vacated_minutes`` defaults to
    the entry's ``base_minutes``.
    """
    vacated_minutes = dict(vacated_minutes or {})
    team_debit_pts = dict(team_debit_pts or {})
    team_total_pre_debit = dict(team_total_pre_debit or {})
    team_total_post_debit = dict(team_total_post_debit or {})
    events: list[dict[str, Any]] = []
    for entry in out_entries or ():
        out_norm = str(entry.get("player_name_norm", ""))
        team = str(entry.get("team", ""))
        status = str(entry.get("status", ""))
        if not out_norm or not team:
            continue
        try:
            vacated = float(
                vacated_minutes.get(out_norm, entry.get("base_minutes", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            vacated = 0.0
        credits = {
            norm: round(float(amount), 4)
            for norm, amount in (attribution.get(out_norm) or {}).items()
        }
        events.append(
            {
                "event_id": make_event_id(screen_date, team, out_norm, status),
                "screen_date": screen_date,
                "team": team,
                "out_player_norm": out_norm,
                "status": status,
                "status_norm": normalize_status(status),
                "vacated_minutes": round(vacated, 4),
                "recipients": sorted(credits),
                "attribution": credits,
                "attribution_total": round(sum(credits.values()), 4),
                "team_debit_pts": team_debit_pts.get(team),
                "team_total_pre_debit": team_total_pre_debit.get(team),
                "team_total_post_debit": team_total_post_debit.get(team),
            }
        )
    events.sort(key=lambda event: event["event_id"])
    return events


def append_injury_events(path: Path, events: Iterable[Mapping[str, Any]]) -> int:
    """Append events as JSONL; skips ``event_id`` duplicates already on disk."""
    events = list(events)
    if not events:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                existing.add(str(json.loads(line).get("event_id", "")))
            except (json.JSONDecodeError, AttributeError):
                continue
    added = 0
    with path.open("a") as handle:
        for event in events:
            event_id = str(event.get("event_id", ""))
            if event_id and event_id in existing:
                continue
            handle.write(json.dumps(dict(event), sort_keys=True) + "\n")
            if event_id:
                existing.add(event_id)
            added += 1
    return added


def load_injury_events(path: Path) -> list[dict[str, Any]]:
    """Load all JSONL events from ``path`` (empty list when missing)."""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def events_for_date(
    events: Iterable[Mapping[str, Any]], screen_date: str
) -> list[dict[str, Any]]:
    """Filter loaded events to one forecast date, sorted by event id."""
    selected = [
        dict(event)
        for event in events
        if str(event.get("screen_date", "")) == screen_date
    ]
    selected.sort(key=lambda event: str(event.get("event_id", "")))
    return selected
