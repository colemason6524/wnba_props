"""Injury-debit observability: debit fields surface on game boards + ledger."""
from __future__ import annotations
import contextlib
import io
import unittest
from datetime import date, datetime, timedelta, timezone
from wnba_props.board import build_daily_board
from wnba_props.features.team import TeamGameResult
from wnba_props.models import Game, PlayerGameLog
from wnba_props.modeling.game import RidgeModel, fit_logistic
from wnba_props.modeling.game_forecast import GameEngine, GameMarket
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
def _league_logs():
    return (
        _logs("Allisha Gray", "allisha gray", "ATL", "CHI", 29.0, pts=17)
        + _logs("Rhyne Howard", "rhyne howard", "ATL", "CHI", 28.0, pts=16)
        + _logs("Bench Wing", "bench wing", "ATL", "CHI", 12.0, pts=5)
        + _logs("Chi Star", "chi star", "CHI", "ATL", 29.0, pts=18)
        + _logs("Chi Role", "chi role", "CHI", "ATL", 18.0, pts=8)
    )
def _team_results():
    return [
        TeamGameResult(BASE - timedelta(days=d), "ATL", "CHI", score, 80, home=True)
        for d, score in ((1, 88), (3, 86), (5, 84))
    ] + [
        TeamGameResult(BASE - timedelta(days=d), "CHI", "ATL", score, 88, home=False)
        for d, score in ((1, 80), (3, 78), (5, 76))
    ]
def _engine():
    return GameEngine(
        winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=200),
        margin=RidgeModel(
            coefficients=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], intercept=0.0
        ),
        total=RidgeModel(coefficients=[1.0, 0.0, 0.0, 0.0], intercept=0.0),
    )
def _board(logs, statuses):
    return build_daily_board(
        screen_date="2026-06-01",
        run_id="run-inj-obs",
        slate=[
            Game(
                "evt-1",
                BASE,
                datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc),
                "ATL",
                "CHI",
                "fixture",
            )
        ],
        prop_lines=[],
        league_logs=logs,
        team_results=_team_results(),
        game_engine=_engine(),
        residual_artifacts={},
        game_markets={
            ("CHI", "ATL"): GameMarket(
                home_moneyline=-150,
                away_moneyline=130,
                home_spread=-4.5,
                home_spread_price=-110,
                away_spread_price=-110,
                total_line=170.5,
                over_price=-110,
                under_price=-110,
            )
        },
        player_statuses=statuses,
        simulations=200,
    )
class InjuryDebitObservabilityTests(unittest.TestCase):
    def test_debit_fields_on_board_rows_and_ledger(self) -> None:
        logs = _league_logs()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            healthy = _board(logs, {})
            out = _board(logs, {"allisha gray": "Out"})
        for board in (healthy, out):
            self.assertIn("Spread", board.sections)
            self.assertIn("Totals", board.sections)
        for row in healthy.sections["Spread"] + healthy.sections["Totals"]:
            self.assertEqual(row["injury_debit_pts_home"], 0.0)
            self.assertEqual(row["injury_debit_pts_away"], 0.0)
        for row in healthy.ledger_rows:
            if row["market"] in ("SPREAD", "TOTAL"):
                self.assertEqual(row["injury_debit_pts_home"], 0.0)
                self.assertEqual(row["injury_debit_pts_away"], 0.0)
        out_totals = [r for r in out.sections["Totals"]]
        out_spreads = [r for r in out.sections["Spread"]]
        self.assertEqual(len(out_totals), 1)
        self.assertEqual(len(out_spreads), 1)
        self.assertAlmostEqual(out_totals[0]["injury_debit_pts_home"], 11.0)
        self.assertEqual(out_totals[0]["injury_debit_pts_away"], 0.0)
        self.assertAlmostEqual(out_spreads[0]["injury_debit_pts_home"], 11.0)
        self.assertEqual(out_spreads[0]["injury_debit_pts_away"], 0.0)
        healthy_total = healthy.sections["Totals"][0]["projection"]
        healthy_margin = healthy.sections["Spread"][0]["projection"]
        self.assertAlmostEqual(
            out_totals[0]["total_pre_debit"], healthy_total, places=5
        )
        self.assertAlmostEqual(
            out_totals[0]["total_post_debit"],
            out_totals[0]["projection"],
            places=5,
        )
        self.assertAlmostEqual(
            out_spreads[0]["margin_pre_debit"], healthy_margin, places=5
        )
        self.assertAlmostEqual(
            out_spreads[0]["margin_post_debit"],
            out_spreads[0]["projection"],
            places=5,
        )
        out_ledger_total = [
            r for r in out.ledger_rows if r["market"] == "TOTAL"
        ]
        self.assertEqual(len(out_ledger_total), 1)
        self.assertAlmostEqual(
            out_ledger_total[0]["injury_debit_pts_home"], 11.0
        )
        self.assertAlmostEqual(
            out_ledger_total[0]["total_pre_debit"], healthy_total, places=5
        )
        logged = [
            line
            for line in buf.getvalue().splitlines()
            if "[team-injury]" in line
        ]
        self.assertEqual(len(logged), 1)
        self.assertIn("ATL", logged[0])
if __name__ == "__main__":
    unittest.main()
