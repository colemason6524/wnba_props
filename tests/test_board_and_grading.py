from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from wnba_props.board import (
    assemble_board,
    build_daily_board,
    health_report,
    write_board,
    write_ledger,
)
from wnba_props.features.team import TeamGameResult
from wnba_props.models import Game, PlayerGameLog, PropLine
from wnba_props.modeling.calibration import ResidualArtifact
from wnba_props.modeling.game import fit_logistic, fit_ridge
from wnba_props.grading import (
    grade_over_under,
    grade_spread_pick,
    grade_total_pick,
    grade_winner_pick,
    proposition_id,
    settle_units,
)
from wnba_props.ledger import (
    LOSS,
    PENDING,
    PUSH,
    VOID,
    WIN,
    append_rows,
    load_rows,
    roi_summary,
    settle_rows,
)
from wnba_props.modeling.game_forecast import GameEngine, GameForecast, GameMarket
from wnba_props.modeling.props_forecast import PropForecast
from wnba_props.notifiers.forecast_discord import (
    _has_rows,
    chunk_message,
    render_board,
    split_sections,
)


def _game_forecast() -> GameForecast:
    return GameForecast(
        home_team="NY",
        away_team="PHX",
        game_date="2026-06-01",
        p_home=0.62,
        p_away=0.38,
        margin_projection=4.2,
        total_projection=166.0,
        winner_pick="HOME",
        winner_probability=0.62,
        winner_price=-150,
        winner_ev=0.08,
        winner_value="FAVORABLE",
        spread_pick="HOME",
        spread_probability=0.58,
        spread_price=-110,
        spread_ev=0.14,
        spread_value="FAVORABLE",
        spread_line=-3.5,
        total_pick="OVER",
        total_probability=0.54,
        total_price=-105,
        total_ev=0.06,
        total_value="FAVORABLE",
        total_line=164.5,
    )


def _prop_forecast() -> PropForecast:
    return PropForecast(
        player_name_raw="Caitlin Clark",
        player_name_norm="caitlin clark",
        team="IND",
        opponent="CON",
        game_date=date(2026, 6, 1),
        prop_type="PTS",
        line=21.5,
        bookmaker="fanduel",
        pick_side="OVER",
        pick_probability=0.62,
        over_probability=0.62,
        under_probability=0.38,
        push_probability=0.0,
        projected_mean=23.0,
        projected_minutes=34.0,
        projected_rate=0.68,
        percentile_10=15.0,
        percentile_90=31.0,
        price=-110,
        ev=0.10,
        value_label="FAVORABLE",
        over_odds=-110,
        under_odds=-110,
    )


class GradingTests(unittest.TestCase):
    def test_over_under(self) -> None:
        self.assertEqual(grade_over_under("OVER", 25, 21.5), WIN)
        self.assertEqual(grade_over_under("OVER", 20, 21.5), LOSS)
        self.assertEqual(grade_over_under("UNDER", 20, 21.5), WIN)
        self.assertEqual(grade_over_under("OVER", 21, 21), PUSH)

    def test_settle_units(self) -> None:
        self.assertAlmostEqual(settle_units(WIN, -110), 100 / 110)
        self.assertEqual(settle_units(LOSS, -110), -1.0)
        self.assertEqual(settle_units(PUSH, -110), 0.0)

    def test_spread_and_total(self) -> None:
        forecast = _game_forecast()
        self.assertEqual(grade_spread_pick(forecast, 90, 84), WIN)
        self.assertEqual(grade_spread_pick(forecast, 80, 84), LOSS)
        self.assertEqual(grade_total_pick(forecast, 90, 80), WIN)
        self.assertEqual(grade_total_pick(forecast, 80, 80), LOSS)

    def test_winner(self) -> None:
        forecast = _game_forecast()
        self.assertEqual(grade_winner_pick(forecast, 90, 80), WIN)
        self.assertEqual(grade_winner_pick(forecast, 70, 80), LOSS)


class LedgerTests(unittest.TestCase):
    def test_append_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            row = {"run_id": "r1", "proposition_id": "p1", "outcome": PENDING, "price": -110}
            self.assertEqual(append_rows(path, [row]), 1)
            self.assertEqual(append_rows(path, [row]), 0)
            self.assertEqual(len(load_rows(path)), 1)

    def test_settle_updates_pending_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            append_rows(
                path,
                [
                    {"run_id": "r1", "proposition_id": "p1", "outcome": PENDING, "price": -110},
                    {"run_id": "r1", "proposition_id": "p2", "outcome": PENDING, "price": -110},
                ],
            )
            updated = settle_rows(path, {"p1": (WIN, 0.91)})
            self.assertEqual(updated, 1)
            rows = {r["proposition_id"]: r for r in load_rows(path)}
            self.assertEqual(rows["p1"]["outcome"], WIN)
            self.assertEqual(rows["p2"]["outcome"], PENDING)
            summary = roi_summary(load_rows(path))
            self.assertEqual(summary["plays"], 1)


    def test_later_slot_supersedes_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            afternoon = {
                "run_id": "forecast-2026-06-01-afternoon",
                "proposition_id": "PTS:a:2026-06-01:20.5",
                "market": "PTS",
                "subject": "A",
                "game_date": "2026-06-01",
                "price": -110,
                "outcome": PENDING,
            }
            evening = dict(
                afternoon,
                run_id="forecast-2026-06-01-evening",
                proposition_id="PTS:a:2026-06-01:21.0",
                price=-115,
            )
            self.assertEqual(append_rows(path, [afternoon]), 1)
            self.assertEqual(append_rows(path, [evening]), 1)
            rows = load_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["price"], -115)
            self.assertEqual(rows[0]["run_id"], "forecast-2026-06-01-evening")

    def test_settled_row_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            row = {
                "run_id": "forecast-2026-06-01-afternoon",
                "proposition_id": "PTS:a:2026-06-01:20.5",
                "market": "PTS",
                "subject": "A",
                "game_date": "2026-06-01",
                "price": -110,
                "outcome": PENDING,
            }
            append_rows(path, [row])
            settle_rows(path, {"PTS:a:2026-06-01:20.5": (WIN, 0.91)})
            duplicate = dict(
                row,
                run_id="forecast-2026-06-01-evening",
                proposition_id="PTS:a:2026-06-01:21.0",
                price=-115,
            )
            self.assertEqual(append_rows(path, [duplicate]), 0)
            rows = load_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], WIN)


class BoardTests(unittest.TestCase):
    def test_assemble_and_render(self) -> None:
        board = assemble_board(
            screen_date="2026-06-01",
            run_id="run-1",
            game_forecasts=[_game_forecast()],
            prop_forecasts=[_prop_forecast()],
        )
        self.assertIn("Moneyline", board.sections)
        self.assertIn("Points", board.sections)
        self.assertEqual(board.summary["game_rows"], 3)
        self.assertEqual(board.summary["prop_rows"], 1)
        text = render_board(board.sections, screen_date="2026-06-01")
        self.assertIn("== Moneyline (1) ==", text)
        self.assertIn("== Points (1) ==", text)
        self.assertIn("Caitlin Clark", text)

    def test_write_board_and_ledger(self) -> None:
        board = assemble_board(
            screen_date="2026-06-01",
            run_id="run-1",
            prop_forecasts=[_prop_forecast()],
        )
        with tempfile.TemporaryDirectory() as tmp:
            board_path = Path(tmp) / "board.json"
            ledger_path = Path(tmp) / "ledger.jsonl"
            write_board(board_path, board)
            write_ledger(ledger_path, board)
            payload = json.loads(board_path.read_text())
            self.assertEqual(payload["screen_date"], "2026-06-01")
            self.assertEqual(len(load_rows(ledger_path)), 1)

    def test_proposition_id(self) -> None:
        self.assertEqual(
            proposition_id("PTS", "caitlin clark", "2026-06-01", 21.5),
            "PTS:caitlin clark:2026-06-01:21.5",
        )

    def test_split_sections_separates_team_and_player(self) -> None:
        board = assemble_board(
            screen_date="2026-06-01",
            run_id="run-1",
            game_forecasts=[_game_forecast()],
            prop_forecasts=[_prop_forecast()],
        )
        team, player = split_sections(board.sections)
        self.assertTrue(_has_rows(team))
        self.assertTrue(_has_rows(player))
        self.assertIn("Moneyline", team)
        self.assertIn("Points", player)
        self.assertNotIn("Points", team)
        self.assertNotIn("Moneyline", player)
        text = render_board(player, screen_date="2026-06-01", title="WNBA Player Props")
        self.assertIn("WNBA Player Props", text)
        self.assertNotIn("== Moneyline", text)


class HealthTests(unittest.TestCase):
    def _board(self):
        return assemble_board(
            screen_date="2026-06-01",
            run_id="run-1",
            game_forecasts=[_game_forecast()],
            prop_forecasts=[_prop_forecast()],
        )

    def test_ok_when_thresholds_met(self) -> None:
        report = health_report(
            self._board(),
            require_priced=True,
            slate_games=1,
            games_with_markets=1,
            min_event_match_ratio=1.0,
            evaluated_lines=1,
            min_player_load_ratio=0.9,
        )
        self.assertEqual(report["status"], "ok")

    def test_degraded_on_partial_game_coverage(self) -> None:
        report = health_report(
            self._board(),
            require_priced=True,
            slate_games=4,
            games_with_markets=3,
            min_event_match_ratio=1.0,
        )
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(any("game-market coverage" in r for r in report["reasons"]))

    def test_degraded_on_low_player_coverage(self) -> None:
        report = health_report(
            self._board(),
            require_priced=True,
            evaluated_lines=50,
            min_player_load_ratio=0.9,
        )
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(any("player coverage" in r for r in report["reasons"]))


class IntegrationTests(unittest.TestCase):
    def test_build_daily_board_with_props_and_game(self) -> None:
        from datetime import timedelta

        base = date(2026, 6, 1)
        logs = []
        for index in range(10):
            day = base - timedelta(days=index + 1)
            logs.append(
                PlayerGameLog(
                    player_name_raw="Test Player",
                    player_name_norm="test player",
                    game_date=day,
                    team="NY",
                    opponent="PHX",
                    minutes=32.0,
                    points=22,
                    rebounds=6,
                    assists=5,
                    threes_made=2,
                    did_play=True,
                    source="fixture",
                )
            )
        prop_line = PropLine(
            event_id="evt-1",
            game_date=base,
            player_name_raw="Test Player",
            player_name_norm="test player",
            team="NY",
            opponent="PHX",
            prop_type="PTS",
            line=20.5,
            bookmaker="fanduel",
            source="fixture",
            collected_at=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc),
            over_odds=-110,
            under_odds=-110,
        )
        artifact = ResidualArtifact(
            schema_version=1,
            model_id="test",
            prop_type="PTS",
            source_model_version="v2",
            n_rows=20,
            calibration_lambda=1.0,
            pairs=tuple((0.0, 0.0) for _ in range(20)),
            sha256="deadbeef",
        )
        team_results = [
            TeamGameResult(base - timedelta(days=d), "NY", "PHX", 88, 80, home=True)
            for d in (1, 3, 5)
        ] + [
            TeamGameResult(base - timedelta(days=d), "PHX", "NY", 80, 88, home=False)
            for d in (1, 3, 5)
        ]
        engine = GameEngine(
            winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=200),
            margin=fit_ridge([[1.0], [-1.0]], [6.0, -6.0], l2=0.0),
            total=fit_ridge([[1.0], [-1.0]], [168.0, 160.0], l2=0.0),
        )
        board = build_daily_board(
            screen_date="2026-06-01",
            run_id="run-x",
            slate=[
                Game(
                    "evt-1",
                    base,
                    datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc),
                    "NY",
                    "PHX",
                    "fixture",
                )
            ],
            prop_lines=[prop_line],
            league_logs=logs,
            team_results=team_results,
            game_engine=engine,
            residual_artifacts={"PTS": artifact},
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
            simulations=500,
        )
        self.assertIn("Points", board.sections)
        self.assertEqual(board.summary["prop_rows"], 1)
        self.assertGreaterEqual(board.summary["game_rows"], 3)


class LedgerGradingTests(unittest.TestCase):
    def _stats(self, **kwargs):
        from types import SimpleNamespace

        base = dict(minutes=30.0, points=20, rebounds=7, assists=5, threes_made=2)
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_game_markets(self) -> None:
        from grade_forecast_board import grade_ledger_row

        scores = {("ATL", "CON"): (90, 80)}
        ml = dict(market="ML", pick="HOME", line=None, price=-110, subject="CON @ ATL")
        self.assertEqual(grade_ledger_row(ml, scores, {})[0], WIN)
        spread = dict(market="SPREAD", pick="HOME", line=-14.5, price=-110, subject="CON @ ATL")
        self.assertEqual(grade_ledger_row(spread, scores, {})[0], LOSS)
        total = dict(market="TOTAL", pick="OVER", line=165.0, price=-110, subject="CON @ ATL")
        self.assertEqual(grade_ledger_row(total, scores, {})[0], WIN)
        push = dict(market="TOTAL", pick="OVER", line=170.0, price=-110, subject="CON @ ATL")
        self.assertEqual(grade_ledger_row(push, scores, {})[0], PUSH)

    def test_props_and_void(self) -> None:
        from grade_forecast_board import grade_ledger_row

        stats = {"caitlin clark": self._stats(points=25)}
        row = dict(market="PTS", pick="OVER", line=21.5, price=-110, subject="Caitlin Clark")
        self.assertEqual(grade_ledger_row(row, {}, stats)[0], WIN)
        dnp = {"caitlin clark": self._stats(points=0, minutes=0.0)}
        self.assertEqual(grade_ledger_row(row, {}, dnp)[0], VOID)
        missing = dict(market="REB", pick="UNDER", line=5.5, price=-110, subject="Nobody")
        self.assertEqual(grade_ledger_row(missing, {}, {})[0], PENDING)

    def test_summarize_and_recap(self) -> None:
        from grade_forecast_board import _summarize

        rows = [
            {"market": "ML", "outcome": WIN, "units": 0.91},
            {"market": "ML", "outcome": LOSS, "units": -1.0},
            {"market": "PTS", "outcome": PUSH, "units": 0.0},
            {"market": "PTS", "outcome": PENDING, "units": None},
        ]
        summary = _summarize(rows)
        self.assertEqual(summary["overall"]["wins"], 1)
        self.assertEqual(summary["overall"]["losses"], 1)
        self.assertEqual(summary["pending"], 1)

        from wnba_props.notifiers.forecast_discord import render_recap

        text = render_recap("2026-09-17", summary)
        self.assertIn("WNBA Forecast Recap - 2026-09-17", text)
        self.assertIn("ML:", text)


class DiscordRenderTests(unittest.TestCase):
    def test_chunk_under_limit(self) -> None:
        text = "\n".join(f"line {i}" for i in range(200))
        chunks = chunk_message(text, limit=100)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertEqual("\n".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
