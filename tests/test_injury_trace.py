"""Injury traceability tests: explained redistribution, event log, board carry-through, adjudication."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from wnba_props.board import build_daily_board, build_injury_events_for_board
from wnba_props.features.team import TeamGameResult
from wnba_props.injury_adjudication import (
    adjudicate_injury_event,
    summarize_adjudication,
)
from wnba_props.injury_trace import (
    append_injury_events,
    build_injury_events,
    events_for_date,
    load_injury_events,
    make_event_id,
)
from wnba_props.injury_trace_report import render_injury_trace_report
from wnba_props.models import Game, PlayerGameLog, PropLine
from wnba_props.modeling.calibration import ResidualArtifact
from wnba_props.modeling.game import fit_logistic, fit_ridge
from wnba_props.modeling.game_forecast import GameEngine, GameMarket
from wnba_props.rotation import (
    redistribute_out_minutes,
    redistribute_out_minutes_explained,
)


BASE = date(2026, 6, 1)


def _logs(name, norm, team, opp, minutes, pts=10, n=10):
    return [
        PlayerGameLog(
            player_name_raw=name,
            player_name_norm=norm,
            game_date=BASE - timedelta(days=i + 1),
            team=team,
            opponent=opp,
            minutes=minutes,
            points=pts,
            rebounds=4,
            assists=3,
            threes_made=1,
            did_play=True,
            source="fixture",
        )
        for i in range(n)
    ]


def _line(name, norm, team, opp, prop="PTS", line=12.5):
    return PropLine(
        event_id="evt-1",
        game_date=BASE,
        player_name_raw=name,
        player_name_norm=norm,
        team=team,
        opponent=opp,
        prop_type=prop,
        line=line,
        bookmaker="fanduel",
        source="fixture",
        collected_at=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc),
        over_odds=-110,
        under_odds=-110,
    )


def _artifact():
    return ResidualArtifact(
        schema_version=1,
        model_id="test",
        prop_type="PTS",
        source_model_version="v2",
        n_rows=20,
        calibration_lambda=1.0,
        pairs=tuple((0.0, 0.0) for _ in range(20)),
        sha256="deadbeef",
    )


def _engine():
    return GameEngine(
        winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=200),
        margin=fit_ridge([[1.0], [-1.0]], [6.0, -6.0], l2=0.0),
        total=fit_ridge([[1.0], [-1.0]], [168.0, 160.0], l2=0.0),
    )


def _team_results():
    return [
        TeamGameResult(BASE - timedelta(days=d), "PHX", "NY", 88, 80, home=True)
        for d in (1, 3, 5)
    ] + [
        TeamGameResult(BASE - timedelta(days=d), "NY", "PHX", 80, 88, home=False)
        for d in (1, 3, 5)
    ]


def _slate():
    logs = (
        _logs("Chelsea Gray", "chelsea gray", "PHX", "NY", 30.0, pts=16)
        + _logs("Dewanna Bonner", "dewanna bonner", "PHX", "NY", 20.0, pts=12)
        + _logs("Bench Wing", "bench wing", "PHX", "NY", 12.0, pts=6)
        + _logs("NY Star", "ny star", "NY", "PHX", 30.0, pts=18)
        + _logs("NY Role", "ny role", "NY", "PHX", 18.0, pts=8)
    )
    lines = [
        _line("Chelsea Gray", "chelsea gray", "PHX", "NY"),
        _line("Dewanna Bonner", "dewanna bonner", "PHX", "NY"),
        _line("Bench Wing", "bench wing", "PHX", "NY"),
        _line("NY Star", "ny star", "NY", "PHX"),
        _line("NY Role", "ny role", "NY", "PHX"),
    ]
    positions = {
        "chelsea gray": "G",
        "dewanna bonner": "F",
        "bench wing": "F",
        "ny star": "G",
        "ny role": "F",
    }
    return logs, lines, positions


def _board(logs, lines, statuses, positions):
    return build_daily_board(
        screen_date="2026-06-01",
        run_id="run-trace",
        slate=[
            Game(
                "evt-1",
                BASE,
                datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc),
                "NY",
                "PHX",
                "fixture",
            )
        ],
        prop_lines=lines,
        league_logs=logs,
        team_results=_team_results(),
        game_engine=_engine(),
        residual_artifacts={"PTS": _artifact()},
        game_markets={
            ("PHX", "NY"): GameMarket(
                home_moneyline=-150,
                away_moneyline=130,
                home_spread=-3.5,
                home_spread_price=-110,
                away_spread_price=-110,
                total_line=164.5,
                over_price=-110,
                under_price=-110,
            )
        },
        player_statuses=statuses,
        player_positions=positions,
        simulations=200,
    )


class ExplainedRedistributionTests(unittest.TestCase):
    def test_wrapper_matches_explained_bumps_exactly(self) -> None:
        roster = {"a": 30.0, "b": 20.0, "c": 12.0, "d": 8.0}
        meta = {
            n: {"team": "PHX", "position": "F", "role": "bench", "depth": i}
            for i, n in enumerate(roster)
        }
        outs = [
            {"player_name_norm": "a", "team": "PHX", "status": "Out",
             "base_minutes": 30.0, "position": "F"},
            {"player_name_norm": "b", "team": "PHX", "status": "Suspended",
             "base_minutes": 20.0, "position": "F"},
        ]
        bumps, attribution = redistribute_out_minutes_explained(outs, roster, meta)
        self.assertEqual(bumps, redistribute_out_minutes(outs, roster, meta))
        # Attribution columns sum back to the reported per-player bumps.
        for recipient, bump in bumps.items():
            credited = sum(contrib.get(recipient, 0.0) for contrib in attribution.values())
            self.assertAlmostEqual(credited, bump, places=9)
        self.assertEqual(set(attribution), {"a", "b"})
        self.assertNotIn("a", bumps)
        self.assertNotIn("b", bumps)

    def test_explained_empty_matches_wrapper(self) -> None:
        roster = {"solo": 30.0}
        meta = {"solo": {"team": "T", "position": "G", "role": "starter", "depth": 0}}
        outs = [{"player_name_norm": "solo", "team": "T", "status": "Out",
                 "base_minutes": 30.0, "position": "G"}]
        self.assertEqual(redistribute_out_minutes_explained(outs, roster, meta), ({}, {}))
        self.assertEqual(redistribute_out_minutes([], roster, meta), {})


class InjuryEventLogTests(unittest.TestCase):
    def test_event_id_format_and_round_trip(self) -> None:
        self.assertEqual(
            make_event_id("2026-06-01", "PHX", "chelsea gray", "Out"),
            "2026-06-01:PHX:chelsea gray:out",
        )
        self.assertEqual(
            make_event_id("2026-06-01", "PHX", "player x", "Out For Season"),
            "2026-06-01:PHX:player x:out for season",
        )
        events = build_injury_events(
            screen_date="2026-06-01",
            out_entries=[
                {"player_name_norm": "chelsea gray", "team": "PHX",
                 "status": "Out", "base_minutes": 30.0, "position": "G"}
            ],
            attribution={"chelsea gray": {"dewanna bonner": 3.0}},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "2026-06-01:PHX:chelsea gray:out")
        self.assertEqual(events[0]["recipients"], ["dewanna bonner"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "injury_events.jsonl"
            self.assertEqual(append_injury_events(path, events), 1)
            self.assertEqual(append_injury_events(path, events), 0)  # dedupe
            loaded = load_injury_events(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["event_id"], events[0]["event_id"])
            self.assertEqual(events_for_date(loaded, "2026-06-02"), [])
            self.assertEqual(len(events_for_date(loaded, "2026-06-01")), 1)
        self.assertEqual(load_injury_events(Path(tempfile.mkdtemp()) / "missing.jsonl"), [])


class BoardTraceTests(unittest.TestCase):
    def test_board_carries_bump_fields_and_trace(self) -> None:
        logs, lines, positions = _slate()
        board = _board(logs, lines, {"chelsea gray": "Out"}, positions)
        bonner_rows = [
            row for row in board.ledger_rows
            if row["market"] == "PTS" and "bonner" in str(row["subject"]).lower()
        ]
        self.assertEqual(len(bonner_rows), 1)
        row = bonner_rows[0]
        self.assertGreater(row["rotation_bump"], 0.0)
        self.assertGreater(row["base_minutes"], 0.0)
        self.assertAlmostEqual(
            row["pre_bump_minutes"], row["base_minutes"], places=5
        )
        self.assertAlmostEqual(
            row["projected_minutes"],
            row["pre_bump_minutes"] + row["rotation_bump"],
            places=2,
        )
        self.assertEqual(row["injury_event_ids"], ["2026-06-01:PHX:chelsea gray:out"])
        self.assertGreaterEqual(row["starter_prob"], 0.0)
        self.assertLessEqual(row["starter_prob"], 1.0)
        trace = board.injury_trace
        self.assertEqual(trace["event_count"], 1)
        self.assertEqual(trace["events"][0]["event_id"], "2026-06-01:PHX:chelsea gray:out")

    def test_healthy_board_trace_empty_but_fields_present(self) -> None:
        logs, lines, positions = _slate()
        board = _board(logs, lines, {}, positions)
        self.assertEqual(board.injury_trace["event_count"], 0)
        self.assertEqual(board.injury_trace["events"], [])
        for row in board.ledger_rows:
            if row["market"] == "PTS":
                self.assertEqual(row["rotation_bump"], 0.0)
                self.assertEqual(row["injury_event_ids"], [])


class AdjudicationTests(unittest.TestCase):
    def _adjudicated(self):
        logs, lines, positions = _slate()
        board = _board(logs, lines, {"chelsea gray": "Out"}, positions)
        event = board.injury_trace["events"][0]
        bonner = next(
            row for row in board.ledger_rows
            if row["market"] == "PTS" and "bonner" in str(row["subject"]).lower()
        )
        adj = adjudicate_injury_event(
            event=event,
            ledger_rows=board.ledger_rows,
            player_actuals={
                "dewanna bonner": {"minutes": 28.0, "PTS": 15},
                "bench wing": {"minutes": 14.0, "PTS": 7},
            },
            team_actuals={"PHX": 84.0, "NY": 80.0},
            opponent_actuals={"PHX": 80.0, "NY": 84.0},
        )
        return event, bonner, adj

    def test_pred_vs_actual_recipient_and_team(self) -> None:
        event, bonner, adj = self._adjudicated()
        by_player = {r["player_norm"]: r for r in adj["recipients"]}
        bonner_adj = by_player["dewanna bonner"]
        self.assertAlmostEqual(
            bonner_adj["predicted_minutes"], bonner["projected_minutes"], places=5
        )
        self.assertEqual(bonner_adj["actual_minutes"], 28.0)
        self.assertTrue(bonner_adj["starter_realized"])  # >= 20 min
        self.assertFalse(by_player["bench wing"]["starter_realized"])
        self.assertIsNotNone(adj["minutes_mae"])
        self.assertIsNotNone(adj["team_pre_debit"])
        self.assertEqual(adj["team_actual"], 84.0)
        self.assertEqual(adj["opponent_baseline_actual"], 80.0)
        summary = summarize_adjudication([adj])
        self.assertEqual(summary["events"], 1)
        self.assertEqual(summary["starter_realization"]["realized"], 1)

    def test_report_renders(self) -> None:
        event, _bonner, adj = self._adjudicated()
        text = render_injury_trace_report(
            screen_date="2026-06-01",
            events=[event],
            adjudications=[adj],
            summary=summarize_adjudication([adj]),
        )
        self.assertIn("chelsea gray", text)
        self.assertIn("dewanna bonner", text)
        empty = render_injury_trace_report(screen_date="2026-06-02", events=[])
        self.assertIn("No redistributable OUT players", empty)


if __name__ == "__main__":
    unittest.main()
