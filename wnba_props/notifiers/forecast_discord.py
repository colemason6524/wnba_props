from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .discord import DiscordResult, send_discord_message


DISCORD_CHUNK_LIMIT = 1900
DISCORD_SECTION_CAP = 40

SECTION_ORDER = (
    "Moneyline",
    "Spread",
    "Totals",
    "Points",
    "Rebounds",
    "Assists",
    "Three-Pointers",
)

TEAM_SECTIONS = ("Moneyline", "Spread", "Totals")
PLAYER_SECTIONS = ("Points", "Rebounds", "Assists", "Three-Pointers")


def split_sections(
    sections: dict[str, Sequence[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Split board sections into game (team) and player-prop groups."""
    team = {key: list(sections.get(key, [])) for key in TEAM_SECTIONS}
    player = {key: list(sections.get(key, [])) for key in PLAYER_SECTIONS}
    return team, player


def _has_rows(sections: dict[str, Sequence[dict[str, Any]]]) -> bool:
    return any(sections.get(section) for section in SECTION_ORDER)


def render_board(
    sections: dict[str, Sequence[dict[str, Any]]],
    *,
    screen_date: str,
    slot_label: str = "",
    title: str = "WNBA Forecast Board",
) -> str:
    header = f"{title} - {screen_date}"
    if slot_label:
        header += f" ({slot_label})"
    lines = [header]

    for section in SECTION_ORDER:
        rows = list(sections.get(section, []))
        if not rows:
            continue
        rows.sort(key=lambda row: row.get("p_pick") or 0.0, reverse=True)
        lines.append("")
        lines.append(f"== {section} ({len(rows)}) ==")
        for row in rows[:DISCORD_SECTION_CAP]:
            lines.append(_render_row(row))
    return "\n".join(lines)


def _render_row(row: dict[str, Any]) -> str:
    subject = str(row.get("subject", ""))
    pick_label = str(row.get("pick_label", ""))
    line = row.get("line")
    line_txt = f" {_format_line(line)}" if line is not None else ""
    price = row.get("price")
    price_txt = f" @{price:+d}" if price is not None else " (unpriced)"
    p_pick = row.get("p_pick")
    p_txt = f"{p_pick:.0%}" if p_pick is not None else "n/a"
    ev = row.get("ev")
    ev_txt = f"EV {ev:+.2f}" if ev is not None else "EV n/a"
    value = str(row.get("value", "UNPRICED"))
    return f"- {subject} | {pick_label}{line_txt}{price_txt} | p={p_txt} | {ev_txt} [{value}]"


def _format_line(line: Any) -> str:
    try:
        value = float(line)
    except (TypeError, ValueError):
        return str(line)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:g}"


def chunk_message(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    if not text.strip():
        return []
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.splitlines():
        addition = len(line) + 1
        if current and length + addition > limit:
            chunks.append("\n".join(current))
            current = []
            length = 0
        current.append(line)
        length += addition
    if current:
        chunks.append("\n".join(current))
    return chunks


def delivery_already_sent(path: Path, screen_date: str, slot: str) -> bool:
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("screen_date") == screen_date and payload.get("slot") == slot:
            return True
    return False


def record_delivery(path: Path, screen_date: str, slot: str, chunks: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "screen_date": screen_date,
        "slot": slot,
        "chunks": chunks,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def send_board(
    webhook_url: str,
    sections: dict[str, Sequence[dict[str, Any]]],
    *,
    screen_date: str,
    slot: str = "",
    slot_label: str = "",
    title: str = "WNBA Forecast Board",
    delivery_ledger: Optional[Path] = None,
    force_send: bool = False,
    retries: int = 3,
    username: str = "WNBA Forecast",
) -> list[DiscordResult]:
    if delivery_ledger is not None and not force_send:
        if delivery_already_sent(delivery_ledger, screen_date, slot):
            return [DiscordResult(ok=False, error="duplicate_board_suppressed")]

    text = render_board(
        sections, screen_date=screen_date, slot_label=slot_label, title=title
    )
    chunks = chunk_message(text)
    results: list[DiscordResult] = []
    for chunk in chunks:
        results.append(_send_with_retry(webhook_url, chunk, retries, username))
    if delivery_ledger is not None and all(r.ok for r in results) and results:
        record_delivery(delivery_ledger, screen_date, slot, len(chunks))
    return results


def _send_with_retry(
    webhook_url: str,
    content: str,
    retries: int,
    username: str,
) -> DiscordResult:
    last: DiscordResult = DiscordResult(ok=False, error="no_attempt")
    for attempt in range(max(1, retries)):
        last = send_discord_message(webhook_url, content, username=username)
        if last.ok:
            return last
        time.sleep(2 ** attempt)
    return last


def render_recap(screen_date: str, summary: dict) -> str:
    overall = summary.get("overall", {})
    lines = [f"WNBA Forecast Recap - {screen_date}", ""]
    lines.append(
        f"Overall: {overall.get('wins', 0)}-{overall.get('losses', 0)}-{overall.get('pushes', 0)}"
        f" | {overall.get('units', 0.0):+.2f}u"
        + (f" | ROI {overall['roi']:+.1%}" if overall.get("roi") is not None else "")
        + f" ({overall.get('plays', 0)} priced)"
    )
    for market, record in summary.get("by_market", {}).items():
        roi = record.get("roi")
        roi_text = f" ROI {roi:+.1%}" if roi is not None else ""
        lines.append(
            f"{market}: {record.get('wins', 0)}-{record.get('losses', 0)}-{record.get('pushes', 0)}"
            f" | {record.get('units', 0.0):+.2f}u{roi_text}"
        )
    pending = summary.get("pending", 0)
    if pending:
        lines.append(f"Pending/unresolved: {pending}")
    return "\n".join(lines)


def send_recap(
    webhook_url: str,
    *,
    screen_date: str,
    summary: dict,
    retries: int = 3,
    username: str = "WNBA Forecast",
) -> DiscordResult:
    text = render_recap(screen_date, summary)
    return _send_with_retry(webhook_url, text, retries, username)
