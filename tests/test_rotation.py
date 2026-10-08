"""Rotation redistribution tests (Gray OUT -> Bonner bump, clamps, no-ops)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from wnba_props.board import build_daily_board
from wnba_props.features.team import TeamGameResult
from wnba_props.models import Game, PlayerGameLog, PropLine
from wnba_props.modeling.calibration import ResidualArtifact
from wnba_props.modeling.game import fit_logistic, fit_ridge
from wnba_props.modeling.game_forecast import GameEngine, GameMarket
from wnba_props.rotation import (
    MAX_BUMP_RATIO,
    REDIST_FACTOR,
    is_redistributable_out,
    redistribute_out_minutes,
    redistribute_out_minutes_explained,
)


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


def _board(logs, lines, statuses, positions=None):
    return build_daily_board(
        screen_date="2026-06-01",
        run_id="run-rot",
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
        player_positions=positions or {},
        simulations=200,
    )


def _slate():
    # Chelsea Gray (PHX guard, ~30mpg) + Dewanna Bonner (PHX wing/bench, ~20mpg)
    # + one more PHX bench wing + two NY players for the game engine.
    logs = (
        _logs("Chelsea Gray", "chelsea gray", "PHX", "NY", 30.0, pts=16, ast=6)
        + _logs("Dewanna Bonner", "dewanna bonner", "PHX", "NY", 20.0, pts=12, reb=5)
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


def _proj(board, norm):
    for row in board.ledger_rows:
        if row["market"] != "PTS":
            continue
        from wnba_props.grading import proposition_id

        if row["proposition_id"] == proposition_id(
            "PTS", norm, BASE.isoformat(), row["line"]
        ):
            return row["projection"], row["projected_minutes"]
    raise AssertionError(f"no PTS row for {norm}")


class StatusGateTests(unittest.TestCase):
    def test_only_out_ir_suspended_qualify(self) -> None:
        for status in ("out", "Out", "OUT FOR SEASON", "injured reserve", "IR", "suspended"):
            self.assertTrue(is_redistributable_out(status), status)
        for status in ("day-to-day", "Day to Day", "questionable", "doubtful", "probable", "", "active"):
            self.assertFalse(is_redistributable_out(status), status)


class GrayBonnerTests(unittest.TestCase):
    def test_gray_out_bonner_projection_increases_within_cap(self) -> None:
        logs, lines, positions = _slate()
        healthy = _board(logs, lines, {}, positions)
        before_min = _proj(healthy, "dewanna bonner")[1]
        out_board = _board(logs, lines, {"chelsea gray": "Out"}, positions)
        after_proj, after_min = _proj(out_board, "dewanna bonner")
        before_proj = _proj(healthy, "dewanna bonner")[0]
        self.assertGreater(after_min, before_min)
        self.assertGreater(after_proj, before_proj)
        self.assertLessEqual(after_min - before_min, MAX_BUMP_RATIO * before_min + 1e-9)
        # Gray excluded: no PTS row for her.
        with self.assertRaises(AssertionError):
            _proj(out_board, "chelsea gray")

    def test_healthy_players_byte_identical_with_no_outs(self) -> None:
        logs, lines, positions = _slate()
        first = _board(logs, lines, {}, positions)
        second = _board(logs, lines, {}, positions)
        self.assertEqual(first.ledger_rows, second.ledger_rows)

    def test_day_to_day_untouched(self) -> None:
        logs, lines, positions = _slate()
        healthy = _board(logs, lines, {}, positions)
        dtd = _board(logs, lines, {"chelsea gray": "Day-to-Day"}, positions)
        # Rotation never fires for day-to-day: Bonner's projection and
        # minutes identical, and Gray keeps her row (not excluded).
        # (Gray's own row may differ via the pre-existing uncertain-status
        # DNP path in project_minutes -- unrelated to rotation.)
        self.assertEqual(
            _proj(healthy, "dewanna bonner"), _proj(dtd, "dewanna bonner")
        )
        _proj(dtd, "chelsea gray")  # still present, not excluded


class MultiOutTests(unittest.TestCase):
    def test_no_double_counting(self) -> None:
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
        bumps = redistribute_out_minutes(outs, roster, meta)
        self.assertNotIn("a", bumps)
        self.assertNotIn("b", bumps)
        vacated = 30.0 + 20.0
        self.assertLessEqual(sum(bumps.values()), vacated + 1e-9)
        # Single-pass: both OUT players excluded from the pool, so only
        # healthy teammates absorb minutes; result is deterministic.
        self.assertEqual(set(bumps), {"c", "d"})
        again = redistribute_out_minutes(outs, roster, meta)
        self.assertEqual(bumps, again)

    def test_per_player_50pct_cap(self) -> None:
        roster = {"star": 32.0, "scrub": 4.0}
        meta = {
            "star": {"team": "T", "position": "G", "role": "starter", "depth": 0},
            "scrub": {"team": "T", "position": "G", "role": "bench", "depth": 1},
        }
        outs = [{"player_name_norm": "star", "team": "T", "status": "Out",
                 "base_minutes": 32.0, "position": "G"}]
        bumps = redistribute_out_minutes(outs, roster, meta)
        capped = MAX_BUMP_RATIO * 4.0
        self.assertLessEqual(bumps["scrub"], capped + 1e-9)

    def test_empty_pool_noop(self) -> None:
        roster = {"solo": 30.0}
        meta = {"solo": {"team": "T", "position": "G", "role": "starter", "depth": 0}}
        outs = [{"player_name_norm": "solo", "team": "T", "status": "Out",
                 "base_minutes": 30.0, "position": "G"}]
        self.assertEqual(redistribute_out_minutes(outs, roster, meta), {})
        self.assertEqual(redistribute_out_minutes([], roster, meta), {})


if __name__ == "__main__":
    unittest.main()


class ExplainedParityTests(unittest.TestCase):
    def test_explained_wrapper_identical_and_attribution_sums_back(self) -> None:
        roster = {"a": 30.0, "b": 20.0, "c": 12.0, "d": 8.0}
        meta = {
            n: {"team": "PHX", "position": "F", "role": "bench", "depth": i}
            for i, n in enumerate(roster)
        }
        outs = [
            {"player_name_norm": "a", "team": "PHX", "status": "Out",
             "base_minutes": 30.0, "position": "F"},
            {"player_name_norm": "b", "team": "PHX", "status": "IR",
             "base_minutes": 20.0, "position": "F"},
        ]
        bumps, attribution = redistribute_out_minutes_explained(outs, roster, meta)
        self.assertEqual(bumps, redistribute_out_minutes(outs, roster, meta))
        for recipient, bump in bumps.items():
            credited = sum(
                contrib.get(recipient, 0.0) for contrib in attribution.values()
            )
            self.assertAlmostEqual(credited, bump, places=9)

    def test_gray_out_explained_matches_wrapper(self) -> None:
        logs, lines, positions = _slate()
        roster_minutes: dict = {}
        meta: dict = {}
        logs_by_player: dict = {}
        for log in logs:
            logs_by_player.setdefault(log.player_name_norm, []).append(log)
        for norm, entries in logs_by_player.items():
            eligible = [log for log in entries if log.did_play and log.minutes > 0.0]
            base = sum(log.minutes for log in eligible) / len(eligible)
            roster_minutes[norm] = base
            latest = max(entries, key=lambda log: log.game_date)
            meta[norm] = {
                "team": latest.team,
                "position": positions.get(norm, ""),
                "role": "starter" if base >= 24.0 else "bench",
                "depth": 0,
            }
        outs = [
            {"player_name_norm": "chelsea gray", "team": "PHX", "status": "Out",
             "base_minutes": roster_minutes["chelsea gray"], "position": "G"}
        ]
        bumps, attribution = redistribute_out_minutes_explained(
            outs, roster_minutes, meta
        )
        self.assertEqual(bumps, redistribute_out_minutes(outs, roster_minutes, meta))
        self.assertIn("chelsea gray", attribution)
