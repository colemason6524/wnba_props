"""Team-injury adjustment tests (OUT starter lowers team scoring features)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from wnba_props.board import build_daily_board
from wnba_props.features.team import (
    TeamGameResult,
    build_team_features,
    out_starter_points_above_replacement,
)
from wnba_props.models import Game, PlayerGameLog
from wnba_props.modeling.game import RidgeModel, fit_logistic
from wnba_props.modeling.game_forecast import GameEngine, GameMarket, forecast_game


BASE = date(2026, 6, 1)


def _logs(name, norm, team, opp, minutes, pts=10, reb=4, ast=3, n=10):
    return [
        PlayerGameLog(
            player_name_raw=name,
            player_name_norm=norm,
            game_date=BASE - timedelta(days=i + 1),
            team=team,
            opponent=opp,
            minutes=minutes,
            points=pts,
            rebounds=reb,
            assists=ast,
            threes_made=1,
            did_play=True,
            source="fixture",
        )
        for i in range(n)
    ]


def _league_logs():
    # Allisha Gray (ATL Dream starter, ~29mpg / ~17ppg) plus ATL depth and
    # two CHI players so both teams resolve features.
    return (
        _logs("Allisha Gray", "allisha gray", "ATL", "CHI", 29.0, pts=17, ast=3)
        + _logs("Rhyne Howard", "rhyne howard", "ATL", "CHI", 28.0, pts=16, ast=4)
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
    # Deterministic engine: margin tracks home/away ppg_last_5 diff exactly,
    # total tracks ppg_sum exactly, so injury debits map 1:1 to projections.
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
        run_id="run-inj",
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


def _game_projections(board):
    totals = [
        row["projection"] for row in board.ledger_rows if row["market"] == "TOTAL"
    ]
    spreads = [
        row["projection"] for row in board.ledger_rows if row["market"] == "SPREAD"
    ]
    assert len(totals) == 1, board.ledger_rows
    assert len(spreads) == 1, board.ledger_rows
    return totals[0], spreads[0]


def _logs_by_player(logs):
    by_player: dict[str, list[PlayerGameLog]] = {}
    for log in logs:
        by_player.setdefault(log.player_name_norm, []).append(log)
    return by_player


class GrayOutTests(unittest.TestCase):
    def test_out_starter_lowers_total_projection(self) -> None:
        logs = _league_logs()
        healthy_total, _ = _game_projections(_board(logs, {}))
        out_total, _ = _game_projections(_board(logs, {"allisha gray": "Out"}))
        self.assertLess(out_total, healthy_total)
        # Gray (17ppg) over a 6.0 replacement level debits exactly 11 points.
        self.assertAlmostEqual(healthy_total - out_total, 11.0)

    def test_out_starter_moves_margin_against_their_team(self) -> None:
        logs = _league_logs()
        _, healthy_margin = _game_projections(_board(logs, {}))
        _, out_margin = _game_projections(_board(logs, {"allisha gray": "Out"}))
        # Gray plays for ATL (home): losing her scoring moves margin vs ATL.
        self.assertLess(out_margin, healthy_margin)
        self.assertAlmostEqual(healthy_margin - out_margin, 11.0)

    def test_healthy_rosters_byte_identical(self) -> None:
        logs = _league_logs()
        healthy = _board(logs, {})
        day_to_day = _board(logs, {"allisha gray": "Day-to-Day"})
        self.assertEqual(healthy.ledger_rows, day_to_day.ledger_rows)

    def test_healthy_board_matches_unadjusted_forecast(self) -> None:
        logs = _league_logs()
        board = _board(logs, {})
        board_total, board_margin = _game_projections(board)
        home = build_team_features(
            team="ATL",
            opponent="CHI",
            game_date=BASE,
            results=_team_results(),
            is_home=True,
        )
        away = build_team_features(
            team="CHI",
            opponent="ATL",
            game_date=BASE,
            results=_team_results(),
            is_home=False,
        )
        self.assertIsNotNone(home)
        self.assertIsNotNone(away)
        assert home is not None and away is not None
        direct = forecast_game(
            home=home, away=away, engine=_engine(), market=GameMarket()
        )
        self.assertAlmostEqual(board_total, direct.total_projection, places=5)
        self.assertAlmostEqual(board_margin, direct.margin_projection, places=5)


class StarOutFlagTests(unittest.TestCase):
    def test_star_out_flag_set_on_adjusted_features(self) -> None:
        logs = _logs_by_player(_league_logs())
        results = _team_results()
        healthy = build_team_features(
            team="ATL",
            opponent="CHI",
            game_date=BASE,
            results=results,
            is_home=True,
        )
        out = build_team_features(
            team="ATL",
            opponent="CHI",
            game_date=BASE,
            results=results,
            is_home=True,
            player_statuses={"allisha gray": "Out"},
            logs_by_player=logs,
        )
        self.assertIsNotNone(healthy)
        self.assertIsNotNone(out)
        assert healthy is not None and out is not None
        self.assertFalse(healthy.star_out)
        self.assertEqual(healthy.star_out_points, 0.0)
        self.assertTrue(out.star_out)
        self.assertAlmostEqual(out.star_out_points, 11.0)
        self.assertAlmostEqual(healthy.ppg_last_5 - out.ppg_last_5, 11.0)
        self.assertAlmostEqual(healthy.ppg_last_10 - out.ppg_last_10, 11.0)
        self.assertAlmostEqual(healthy.ppg_season - out.ppg_season, 11.0)
        self.assertAlmostEqual(healthy.margin_last_5 - out.margin_last_5, 11.0)
        # Opponent-defense features untouched.
        self.assertEqual(
            out.opp_ppg_allowed_last_5, healthy.opp_ppg_allowed_last_5
        )
        self.assertEqual(
            out.opp_ppg_allowed_last_10, healthy.opp_ppg_allowed_last_10
        )
        self.assertEqual(
            out.opp_ppg_allowed_season, healthy.opp_ppg_allowed_season
        )

    def test_points_above_replacement_units(self) -> None:
        logs = _logs_by_player(_league_logs())
        self.assertAlmostEqual(
            out_starter_points_above_replacement(
                team="ATL",
                player_statuses={"allisha gray": "Out"},
                logs_by_player=logs,
            ),
            17.0 - 6.0,
        )
        # Day-to-day never counts.
        self.assertEqual(
            out_starter_points_above_replacement(
                team="ATL",
                player_statuses={"allisha gray": "Day-to-Day"},
                logs_by_player=logs,
            ),
            0.0,
        )
        # Bench minutes (< 24) never count even when OUT.
        self.assertEqual(
            out_starter_points_above_replacement(
                team="ATL",
                player_statuses={"bench wing": "Out"},
                logs_by_player=logs,
            ),
            0.0,
        )
        # The other team's starter does not debit ATL.
        self.assertEqual(
            out_starter_points_above_replacement(
                team="ATL",
                player_statuses={"chi star": "Out"},
                logs_by_player=logs,
            ),
            0.0,
        )
        self.assertGreater(
            out_starter_points_above_replacement(
                team="CHI",
                player_statuses={"chi star": "Out"},
                logs_by_player=logs,
            ),
            0.0,
        )


class DreamGrayExampleTests(unittest.TestCase):
    def test_dream_gray_example_prints_both_totals(self) -> None:
        logs = _league_logs()
        healthy_total, _ = _game_projections(_board(logs, {}))
        out_total, _ = _game_projections(_board(logs, {"allisha gray": "Out"}))
        print(
            f"\nDream (ATL) healthy total: {healthy_total} "
            f"vs Gray OUT total: {out_total}"
        )
        self.assertLess(out_total, healthy_total)


if __name__ == "__main__":
    unittest.main()
