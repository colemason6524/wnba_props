from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import run_forecast_pipeline as pipeline


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
        tip = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
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


if __name__ == "__main__":
    unittest.main()
