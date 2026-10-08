"""WNBA injury trace report: human-readable per-date attribution summary.

Renders the adjudicated injury events for one forecast date: who was OUT,
who absorbed the minutes, whether the bumped players realized (minutes and
starter threshold), and how the team total fared against the pre-debit
baseline. Observability only: no model logic.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def render_injury_trace_report(
    *,
    screen_date: str,
    events: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> str:
    """Render a plain-text injury trace report for ``screen_date``."""
    events = list(events or ())
    adjudications = list(adjudications or ())
    by_id = {str(a.get("event_id", "")): a for a in adjudications}
    lines = [f"== Injury trace {screen_date} ({len(events)} event(s)) =="]
    if not events:
        lines.append("No redistributable OUT players on this date.")
        return "\n".join(lines) + "\n"
    for event in sorted(events, key=lambda e: str(e.get("event_id", ""))):
        event_id = str(event.get("event_id", ""))
        lines.append(
            f"-- {event.get('out_player_norm', '?')} ({event.get('team', '?')}, "
            f"{event.get('status', '?')}): vacated "
            f"{_fmt(event.get('vacated_minutes'))} min -> "
            f"{_fmt(event.get('attribution_total'))} min redistributed "
            f"to {len(event.get('recipients', ()))} teammate(s)"
        )
        adj = by_id.get(event_id)
        if adj is None:
            for recipient, credit in sorted((event.get("attribution") or {}).items()):
                lines.append(f"   + {recipient}: +{_fmt(credit)} min (pred)")
            continue
        for recipient in adj.get("recipients", ()):
            lines.append(
                f"   + {recipient.get('player_norm')}: "
                f"pred {_fmt(recipient.get('predicted_minutes'))} min "
                f"(bump +{_fmt(recipient.get('predicted_bump'))}) vs actual "
                f"{_fmt(recipient.get('actual_minutes'))} min"
                + (
                    f", {recipient.get('stat_type')} pred "
                    f"{_fmt(recipient.get('predicted_stat'))} vs actual "
                    f"{_fmt(recipient.get('actual_stat'))}"
                    if recipient.get("stat_type")
                    else ""
                )
                + (" [STARTER]" if recipient.get("starter_realized") else "")
            )
        lines.append(
            f"   team {adj.get('team')}: pre-debit {_fmt(adj.get('team_pre_debit'))} "
            f"vs actual {_fmt(adj.get('team_actual'))} "
            f"(err {_fmt(adj.get('team_error_vs_pre_debit'))}); "
            f"opp baseline {_fmt(adj.get('opponent_baseline_actual'))}"
        )
    if summary:
        real = summary.get("starter_realization", {})
        lines.append(
            f"Summary: {summary.get('events', 0)} events, "
            f"{summary.get('recipients', 0)} recipients, "
            f"minutes MAE {_fmt(summary.get('minutes_mae'))}, "
            f"starter realization {real.get('realized', 0)}/"
            f"{real.get('predicted', 0)}, "
            f"team err MAE {_fmt(summary.get('team_error_mae_vs_pre_debit'))}"
        )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)
