"""Print the human-readable injury trace report for a forecast date.

Reads the graded adjudication when present (outputs/grades/injury_trace/),
otherwise adjudicates in memory from the trace log + ledger without
network access (actuals shown as n/a).

Usage:
    python3 scripts/report_injury_trace.py --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wnba_props.config import OUTPUTS_DIR  # noqa: E402
from wnba_props.injury_trace import events_for_date, load_injury_events  # noqa: E402
from wnba_props.injury_trace_report import render_injury_trace_report  # noqa: E402
from wnba_props.ledger import latest_rows, load_rows  # noqa: E402
from wnba_props.injury_adjudication import (  # noqa: E402
    adjudicate_injury_events,
    summarize_adjudication,
)

TRACE_PATH = OUTPUTS_DIR / "ledger" / "injury_events.jsonl"
LEDGER_PATH = OUTPUTS_DIR / "ledger" / "forecast_ledger.jsonl"
GRADES_DIR = OUTPUTS_DIR / "grades" / "injury_trace"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Report the injury trace for a date.")
    parser.add_argument("--date", required=True, help="Screen date YYYY-MM-DD.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    grade_path = GRADES_DIR / f"injury_trace_grade_{args.date}.json"
    if grade_path.exists():
        payload = json.loads(grade_path.read_text())
        events = events_for_date(load_injury_events(TRACE_PATH), args.date)
        if not events:
            events = [
                {
                    "event_id": a.get("event_id", ""),
                    "out_player_norm": a.get("out_player_norm"),
                    "team": a.get("team"),
                    "status": "",
                    "vacated_minutes": a.get("vacated_minutes"),
                    "recipients": [
                        r.get("player_norm") for r in a.get("recipients", ())
                    ],
                    "attribution": {
                        r.get("player_norm"): r.get("predicted_bump")
                        for r in a.get("recipients", ())
                    },
                    "attribution_total": a.get("predicted_bump_total"),
                }
                for a in payload.get("adjudications", ())
            ]
        print(
            render_injury_trace_report(
                screen_date=args.date,
                events=events,
                adjudications=payload.get("adjudications", ()),
                summary=payload.get("summary", {}),
            ),
            end="",
        )
        return 0
    # No grade file: render predictions from the trace log + ledger only.
    events = events_for_date(load_injury_events(TRACE_PATH), args.date)
    rows = [
        row
        for row in latest_rows(load_rows(LEDGER_PATH))
        if str(row.get("game_date", "")) == args.date
    ]
    adjudications = adjudicate_injury_events(
        events=events,
        ledger_rows=rows,
        player_actuals={},
        team_actuals={},
        opponent_actuals={},
    )
    print(
        render_injury_trace_report(
            screen_date=args.date,
            events=events,
            adjudications=adjudications,
            summary=summarize_adjudication(adjudications),
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
