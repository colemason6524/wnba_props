"""WNBA injury trace adjudication: predicted bump vs actual outcome (observability only).

Compares, per injury event, the model's predicted redistribution against what
actually happened: recipient minutes vs predicted bump, recipient productivity
vs projection, bumped-starter realization (actual minutes >= 20), the team's
pre-debit total vs the actual team total, and the opponent's actual total as a
no-debit baseline.

Inputs are plain mappings so this stays decoupled from pipeline internals:
``events`` from the injury trace log, ``ledger_rows`` (latest capture) for the
predictions, ``player_actuals`` mapping player norm to ``{"minutes", stat}``
per prop type, and ``team_actuals`` mapping team abbr to actual points.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: Actual minutes at/above which a bumped player counts as "realized starter".
STARTER_MINUTES_THRESHOLD = 20.0


def adjudicate_injury_event(
    *,
    event: Mapping[str, Any],
    ledger_rows: Sequence[Mapping[str, Any]],
    player_actuals: Mapping[str, Mapping[str, Any]],
    team_actuals: Mapping[str, float],
    opponent_actuals: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Adjudicate one injury event: prediction vs actuals.

    ``player_actuals`` maps player norm to ``{"minutes": float, <STAT>: value}``
    where ``<STAT>`` is the prop-type key (``PTS``/``REB``/``AST``/``3PM``).
    ``team_actuals`` maps team abbr to actual points scored.
    """
    opponent_actuals = dict(opponent_actuals or {})
    team = str(event.get("team", ""))
    event_id = str(event.get("event_id", ""))
    attribution = dict(event.get("attribution") or {})
    total_bump = sum(float(v) for v in attribution.values())

    by_subject: dict[str, list[Mapping[str, Any]]] = {}
    for row in ledger_rows or ():
        raw = str(row.get("subject", ""))
        by_subject.setdefault(raw, []).append(row)
    by_norm = {str(k).lower(): v for k, v in (player_actuals or {}).items()}

    recipients: list[dict[str, Any]] = []
    minutes_mae: list[float] = []
    for recipient_norm, credit in sorted(attribution.items()):
        credit = float(credit)
        pred_row = _find_recipient_row(by_subject, recipient_norm)
        actual = by_norm.get(recipient_norm, {})
        pred_minutes = _pred_minutes(pred_row, event, recipient_norm)
        actual_minutes = _to_float(actual.get("minutes"))
        pred_stat = _to_float((pred_row or {}).get("projection"))
        prop_type = str((pred_row or {}).get("market", ""))
        actual_stat = _to_float(actual.get(prop_type)) if prop_type else None
        starter_realized = (
            actual_minutes is not None
            and actual_minutes >= STARTER_MINUTES_THRESHOLD
        )
        minutes_error = (
            None
            if pred_minutes is None or actual_minutes is None
            else actual_minutes - pred_minutes
        )
        if minutes_error is not None:
            minutes_mae.append(abs(minutes_error))
        recipients.append(
            {
                "player_norm": recipient_norm,
                "predicted_bump": round(credit, 4),
                "predicted_minutes": pred_minutes,
                "actual_minutes": actual_minutes,
                "minutes_error": None
                if minutes_error is None
                else round(minutes_error, 2),
                "predicted_stat": pred_stat,
                "actual_stat": actual_stat,
                "stat_type": prop_type,
                "starter_realized": starter_realized,
            }
        )

    pre_debit = _to_float(event.get("team_total_pre_debit"))
    post_debit = _to_float(event.get("team_total_post_debit"))
    actual_team = _to_float((team_actuals or {}).get(team))
    opponent_actual = _to_float(opponent_actuals.get(team))
    team_error = (
        None
        if pre_debit is None or actual_team is None
        else round(actual_team - pre_debit, 2)
    )
    realized_starters = sum(1 for r in recipients if r["starter_realized"])
    return {
        "event_id": event_id,
        "team": team,
        "out_player_norm": event.get("out_player_norm"),
        "vacated_minutes": event.get("vacated_minutes"),
        "predicted_bump_total": round(total_bump, 4),
        "recipient_count": len(recipients),
        "recipients": recipients,
        "minutes_mae": round(sum(minutes_mae) / len(minutes_mae), 2)
        if minutes_mae
        else None,
        "starter_realization": {
            "realized": realized_starters,
            "predicted": len(recipients),
            "rate": round(realized_starters / len(recipients), 4)
            if recipients
            else None,
        },
        "team_pre_debit": pre_debit,
        "team_post_debit": post_debit,
        "team_actual": actual_team,
        "team_error_vs_pre_debit": team_error,
        "opponent_baseline_actual": opponent_actual,
    }


def adjudicate_injury_events(
    *,
    events: Sequence[Mapping[str, Any]],
    ledger_rows: Sequence[Mapping[str, Any]],
    player_actuals: Mapping[str, Mapping[str, Any]],
    team_actuals: Mapping[str, float],
    opponent_actuals: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Adjudicate a batch of injury events, sorted by event id."""
    results = [
        adjudicate_injury_event(
            event=event,
            ledger_rows=ledger_rows,
            player_actuals=player_actuals,
            team_actuals=team_actuals,
            opponent_actuals=opponent_actuals,
        )
        for event in events or ()
    ]
    results.sort(key=lambda item: str(item.get("event_id", "")))
    return results


def summarize_adjudication(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate adjudication results into headline counters."""
    results = list(results or [])
    maes = [r["minutes_mae"] for r in results if r.get("minutes_mae") is not None]
    realized = sum(int(r.get("starter_realization", {}).get("realized", 0)) for r in results)
    predicted = sum(int(r.get("starter_realization", {}).get("predicted", 0)) for r in results)
    team_errors = [
        abs(float(r["team_error_vs_pre_debit"]))
        for r in results
        if r.get("team_error_vs_pre_debit") is not None
    ]
    return {
        "events": len(results),
        "recipients": sum(int(r.get("recipient_count", 0)) for r in results),
        "predicted_bump_total": round(
            sum(float(r.get("predicted_bump_total", 0.0)) for r in results), 2
        ),
        "minutes_mae": round(sum(maes) / len(maes), 2) if maes else None,
        "starter_realization": {
            "realized": realized,
            "predicted": predicted,
            "rate": round(realized / predicted, 4) if predicted else None,
        },
        "team_error_mae_vs_pre_debit": round(sum(team_errors) / len(team_errors), 2)
        if team_errors
        else None,
    }


def _find_recipient_row(
    by_subject: Mapping[str, list[Mapping[str, Any]]], recipient_norm: str
) -> Mapping[str, Any] | None:
    for subject, rows in by_subject.items():
        if subject.strip().lower() == recipient_norm:
            prop_rows = [r for r in rows if str(r.get("market", "")) in ("PTS", "REB", "AST", "3PM")]
            if prop_rows:
                return prop_rows[0]
            return rows[0] if rows else None
    return None


def _pred_minutes(
    pred_row: Mapping[str, Any] | None,
    event: Mapping[str, Any],
    recipient_norm: str,
) -> float | None:
    if pred_row is None:
        return None
    minutes = _to_float(pred_row.get("projected_minutes"))
    if minutes is not None:
        return minutes
    # Fall back to pre-bump baseline + attributed credit when the ledger row
    # predates the minutes carry-through.
    base = _to_float(pred_row.get("base_minutes", pred_row.get("pre_bump_minutes")))
    credit = _to_float((event.get("attribution") or {}).get(recipient_norm))
    if base is None:
        return None
    return round(base + (credit or 0.0), 2)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result
