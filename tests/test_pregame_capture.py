from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import run_forecast_pipeline as pipeline
from wnba_props.rotation import is_redistributable_out, redistribute_out_minutes


def _game(tip: datetime) -> SimpleNamespace:
    return SimpleNamespace(game_time=tip, home_team="NY", away_team="PHX")


class PregameTargetTests(unittest.TestCase):
    def test_target_is_lead_minutes_before_earliest_future_tip(self) -> None:
        now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        games = [
            _game(datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)),
            _game(datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)),
            _game(datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)),  # started
        ]
        with patch.object(
            pipeline.EspnSlateSource, "fetch_games", return_value=games
        ):
            capture_at, earliest = pipeline._pregame_capture_target(
                date(2026, 9, 27), lead_minutes=75, now=now
            )
        self.assertEqual(
            earliest, datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(
            capture_at, datetime(2026, 9, 27, 16, 45, tzinfo=timezone.utc)
        )

    def test_no_future_games_returns_none(self) -> None:
        now = datetime(2026, 9, 27, 23, 0, tzinfo=timezone.utc)
        games = [_game(datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc))]
        with patch.object(
            pipeline.EspnSlateSource, "fetch_games", return_value=games
        ):
            self.assertEqual(
                pipeline._pregame_capture_target(
                    date(2026, 9, 27), lead_minutes=75, now=now
                ),
                (None, None),
            )


class BoardPathTests(unittest.TestCase):
    def test_board_path_is_unique_per_snapshot(self) -> None:
        first = pipeline._board_path("2026-09-27", "pregame", "snap-a")
        second = pipeline._board_path("2026-09-27", "pregame", "snap-b")
        self.assertNotEqual(first, second)
        self.assertIn("snap-a", first.name)
        self.assertTrue(first.name.startswith("forecast_board_2026-09-27_pregame_"))


class PregameOrchestrationTests(unittest.TestCase):
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            slot="pregame",
            screen="2026-09-27",
            send_discord=False,
            pregame_lead_minutes=75,
            pregame_retry_minutes=15,
            pregame_max_retries=2,
            pregame_wait=True,
        )
        kwargs.update(overrides)
        return kwargs

    def test_no_upcoming_games_exits_without_capture(self) -> None:
        with (
            patch.object(
                pipeline, "_pregame_capture_target", return_value=(None, None)
            ),
            patch.object(pipeline, "_run_pipeline_once") as once,
        ):
            self.assertEqual(pipeline.run_pipeline(**self._base_kwargs()), 0)
        once.assert_not_called()

    def test_retry_until_healthy_then_stop(self) -> None:
        # Relative to real now so the retry window stays open as time passes.
        tip = datetime.now(timezone.utc) + timedelta(hours=4)
        with (
            patch.object(
                pipeline,
                "_pregame_capture_target",
                return_value=(tip - timedelta(minutes=75), tip),
            ),
            patch.object(pipeline, "_sleep_until") as waiter,
            patch.object(pipeline.time, "sleep"),
            patch.object(
                pipeline,
                "_run_pipeline_once",
                side_effect=[(0, False), (0, True)],
            ) as once,
        ):
            self.assertEqual(pipeline.run_pipeline(**self._base_kwargs()), 0)
        waiter.assert_called_once()
        self.assertEqual(once.call_count, 2)

    def test_stops_retrying_close_to_tip(self) -> None:
        tip = datetime.now(timezone.utc) + timedelta(minutes=10)
        with (
            patch.object(
                pipeline,
                "_pregame_capture_target",
                return_value=(tip - timedelta(minutes=75), tip),
            ),
            patch.object(pipeline, "_sleep_until"),
            patch.object(pipeline.time, "sleep") as sleeper,
            patch.object(
                pipeline, "_run_pipeline_once", return_value=(0, False)
            ) as once,
        ):
            self.assertEqual(pipeline.run_pipeline(**self._base_kwargs()), 0)
        self.assertEqual(once.call_count, 1)
        sleeper.assert_not_called()

    def test_non_pregame_slot_runs_exactly_once(self) -> None:
        with patch.object(
            pipeline, "_run_pipeline_once", return_value=(0, True)
        ) as once:
            self.assertEqual(
                pipeline.run_pipeline(
                    **self._base_kwargs(slot="evening", pregame_wait=True)
                ),
                0,
            )
        self.assertEqual(once.call_count, 1)


class RefreshInjuryTests(unittest.TestCase):
    """Late-pregame refresh bypasses the injury cache for a live ESPN pull.

    Mocks at the ``pipeline.EspnInjurySource`` seam; player-log sources are
    stubbed empty so each test isolates the injury path.
    """

    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            screen_date=date(2026, 9, 27),
            sticky_daily_log_cache=False,
            season_phase="regular",
        )

    def _games(self) -> list:
        return [SimpleNamespace(home_team="NY", away_team="PHX")]

    def _lines(self) -> list:
        return [
            SimpleNamespace(
                player_name_norm="jane doe",
                player_name_raw="Jane Doe",
                team="NY",
            )
        ]

    def _collect(self, injury_mock, **overrides):
        kwargs = dict(refresh_injuries=False)
        kwargs.update(overrides)
        with (
            patch.object(
                pipeline.BasketballReferenceSource,
                "fetch_logs",
                return_value=[],
            ),
            patch.object(
                pipeline.EspnGameLogSource, "fetch_logs", return_value=[]
            ),
            patch.object(
                pipeline.EspnBoundedBoxscoreLogsSource,
                "fetch_logs",
                return_value=[],
            ),
            patch.object(
                pipeline.EspnInjurySource,
                "fetch_team_injuries",
                injury_mock,
            ),
        ):
            return pipeline._collect_player_logs(
                self._settings(),
                self._games(),
                self._lines(),
                MagicMock(),
                MagicMock(),
                **kwargs,
            )

    def test_refresh_bypasses_cache_via_force_refresh(self) -> None:
        injury = SimpleNamespace(
            player_name_norm="jane doe", status="Questionable"
        )
        injury_mock = MagicMock(return_value=[injury])
        _logs, statuses, _team_injuries, _freshness = self._collect(
            injury_mock, refresh_injuries=True
        )
        # One live pull per team on the late slot ...
        self.assertEqual(injury_mock.call_count, 2)
        teams = {call.args[0] for call in injury_mock.call_args_list}
        self.assertEqual(teams, {"NY", "PHX"})
        # ... each bypassing JsonCache via force_refresh.
        for call in injury_mock.call_args_list:
            self.assertTrue(call.kwargs.get("force_refresh"))
        # Live statuses still flow into the board path.
        self.assertEqual(statuses.get("jane doe"), "Questionable")

    def test_normal_path_does_not_force_refresh(self) -> None:
        injury_mock = MagicMock(return_value=[])
        self._collect(injury_mock, refresh_injuries=False)
        self.assertEqual(injury_mock.call_count, 2)
        for call in injury_mock.call_args_list:
            self.assertNotIn("force_refresh", call.kwargs)

    def test_failed_live_pull_propagates_instead_of_publishing_from_cache(
        self,
    ) -> None:
        injury_mock = MagicMock(side_effect=RuntimeError("espn down"))
        with self.assertRaises(RuntimeError):
            self._collect(injury_mock, refresh_injuries=True)

    def test_failed_cached_pull_never_blocks_the_board(self) -> None:
        injury_mock = MagicMock(side_effect=RuntimeError("espn down"))
        _logs, statuses, team_injuries, _freshness = self._collect(
            injury_mock, refresh_injuries=False
        )
        self.assertEqual(statuses, {})
        self.assertEqual(team_injuries, {})


class RotationStatusRuleTests(unittest.TestCase):
    """Questionable-style statuses stay IN; only OUT/IR/suspended vacate.

    Mirrors ``wnba_props/rotation.py``: ``is_redistributable_out`` is the
    rule the board path uses to decide whose minutes get redistributed.
    """

    def test_uncertain_statuses_stay_in(self) -> None:
        for status in (
            "questionable",
            "day-to-day",
            "doubtful",
            "probable",
            "game-time decision",
        ):
            with self.subTest(status=status):
                self.assertFalse(is_redistributable_out(status))

    def test_out_and_ir_vacate(self) -> None:
        for status in (
            "out",
            "Out For Season",
            "out indefinitely",
            "injured reserve",
            "IR",
            "suspended",
        ):
            with self.subTest(status=status):
                self.assertTrue(is_redistributable_out(status))

    def _rotation_inputs(self, status: str):
        return (
            [
                {
                    "player_name_norm": "jane doe",
                    "team": "NY",
                    "status": status,
                    "base_minutes": 30.0,
                }
            ],
            {"jane doe": 30.0, "teammate a": 20.0},
            {
                "jane doe": {
                    "team": "NY",
                    "position": "G",
                    "role": "starter",
                    "depth": 1,
                },
                "teammate a": {
                    "team": "NY",
                    "position": "G",
                    "role": "bench",
                    "depth": 5,
                },
            },
        )

    def test_questionable_generates_no_rotation_bump(self) -> None:
        out_players, roster_minutes, meta = self._rotation_inputs(
            "questionable"
        )
        self.assertEqual(
            redistribute_out_minutes(out_players, roster_minutes, meta), {}
        )

    def test_out_vacates_minutes_to_teammates(self) -> None:
        out_players, roster_minutes, meta = self._rotation_inputs("out")
        bumps = redistribute_out_minutes(out_players, roster_minutes, meta)
        self.assertGreater(bumps.get("teammate a", 0.0), 0.0)
        self.assertNotIn("jane doe", bumps)


if __name__ == "__main__":
    unittest.main()
