"""Grade the injury trace for a forecast date: pred vs actual (observability only).

Reads the injury event log and the forecast ledger, resolves actuals from the
ESPN scoreboard/boxscores (same sources as grade_forecast_board.py), and
writes a JSON adjudication plus a human-readable report.

Usage:
    python3 scripts/grade_injury_trace.py --date YYYY-MM-DD [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grade_forecast_board import _final_scores, _player_stats  # noqa: E402
from wnba_props.config import OUTPUTS_DIR  # noqa: E402
from wnba_props.injury_adjudication import (  # noqa: E402
    adjudicate_injury_events,
    summarize_adjudication,
)
from wnba_props.injury_trace import events_for_date, load_injury_events  # noqa: E402
from wnba_props.injury_trace_report import render_injury_trace_report  # noqa: E402
from wnba_props.ledger import latest_rows, load_rows  # noqa: E402

LEDGER_PATH = OUTPUTS_DIR / "ledger" / "forecast_ledger.jsonl"
TRACE_PATH = OUTPUTS_DIR / "ledger" / "injury_events.jsonl"
GRADES_DIR = OUTPUTS_DIR / "grades" / "injury_trace"

_STAT_FIELDS = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "3PM": "threes_made",
}


def grade_injury_trace(screen: date) -> dict:
    """Adjudicate all injury events for ``screen``; returns the grade payload."""
    screen_text = screen.isoformat()
    events = events_for_date(load_injury_events(TRACE_PATH), screen_text)
    rows = [
        row
        for row in latest_rows(load_rows(LEDGER_PATH))
        if str(row.get("game_date", "")) == screen_text
    ]
    try:
        scores = _final_scores(screen)
    except Exception:  # noqa: BLE001 - missing scoreboard leaves team actuals empty
        scores = {}
    try:
        stats = _player_stats(screen)
    except Exception:  # noqa: BLE001 - missing boxscores leave player actuals empty
        stats = {}

    team_actuals: dict[str, float] = {}
    opponent_actuals: dict[str, float] = {}
    for (home, away), (home_score, away_score) in scores.items():
        team_actuals[home] = float(home_score)
        team_actuals[away] = float(away_score)
        opponent_actuals[home] = float(away_score)
        opponent_actuals[away] = float(home_score)

    player_actuals: dict[str, dict] = {}
    for norm, line in stats.items():
        entry: dict[str, float] = {"minutes": float(getattr(line, "minutes", 0.0) or 0.0)}
        for prop_type, field in _STAT_FIELDS.items():
            value = getattr(line, field, None)
            if value is not None:
                entry[prop_type] = float(value)
        player_actuals[str(norm)] = entry

    adjudications = adjudicate_injury_events(
        events=events,
        ledger_rows=rows,
        player_actuals=player_actuals,
        team_actuals=team_actuals,
        opponent_actuals=opponent_actuals,
    )
    summary = summarize_adjudication(adjudications)
    return {
        "screen_date": screen_text,
        "graded_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "events": len(events),
        "summary": summary,
        "adjudications": adjudications,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Grade the injury trace for a date.")
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to yesterday.")
    parser.add_argument("--out", default=None, help="Output dir; defaults to outputs/grades/injury_trace.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    screen = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    payload = grade_injury_trace(screen)
    out_dir = Path(args.out) if args.out else GRADES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"injury_trace_grade_{screen.isoformat()}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    report = render_injury_trace_report(
        screen_date=screen.isoformat(),
        events=events_for_date(
            load_injury_events(TRACE_PATH), screen.isoformat()
        ),
        adjudications=payload["adjudications"],
        summary=payload["summary"],
    )
    (out_dir / f"injury_trace_report_{screen.isoformat()}.txt").write_text(report)
    print(report, end="")
    print(f"[injury-trace] grade -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
