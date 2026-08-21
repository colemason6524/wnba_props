from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from run_projection_shadow import _collected_too_late
from wnba_props.models import Game, PropLine
from wnba_props.shadow.collection import (
    CaptureRegistry,
    CaptureWindow,
    projection_id,
    select_games_in_capture_window,
)


def _game(game_id: str, game_time: datetime) -> Game:
    return Game(
        game_id=game_id,
        game_date=date(2026, 8, 5),
        game_time=game_time,
        home_team="NY",
        away_team="PHX",
        source="fixture",
    )


class ShadowCollectionTests(unittest.TestCase):
    def test_capture_window_includes_boundaries_and_excludes_other_games(self) -> None:
        now = datetime(2026, 8, 5, 22, 0, tzinfo=timezone.utc)
        games = [
            _game("too-soon", now + timedelta(minutes=19)),
            _game("minimum", now + timedelta(minutes=20)),
            _game("middle", now + timedelta(minutes=45)),
            _game("maximum", now + timedelta(minutes=90)),
            _game("too-early", now + timedelta(minutes=91)),
        ]

        selected = select_games_in_capture_window(
            games,
            now=now,
            window=CaptureWindow(20.0, 90.0),
        )

        self.assertEqual(["minimum", "middle", "maximum"], [game.game_id for game in selected])

    def test_capture_registry_persists_and_is_scoped_by_model_and_source(self) -> None:
        now = datetime(2026, 8, 5, 22, 0, tzinfo=timezone.utc)
        game = _game("game-1", now + timedelta(minutes=60))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture_registry.json"
            registry = CaptureRegistry(path)
            self.assertFalse(
                registry.is_captured(
                    game_id="game-1",
                    model_version="model-v1",
                    line_source="playerprops",
                )
            )
            registry.record_attempt(
                games=[game],
                model_version="model-v1",
                line_source="playerprops",
                snapshot_path=Path(directory) / "snapshot.json",
                captured_at=now,
                outcomes={"game-1": "partial"},
            )
            registry.mark_complete(
                games=[game],
                model_version="model-v1",
                line_source="playerprops",
                snapshot_path=Path(directory) / "snapshot.json",
                captured_at=now,
            )

            reloaded = CaptureRegistry(path)
            self.assertTrue(
                reloaded.is_captured(
                    game_id="game-1",
                    model_version="model-v1",
                    line_source="playerprops",
                )
            )
            self.assertFalse(
                reloaded.is_captured(
                    game_id="game-1",
                    model_version="model-v2",
                    line_source="playerprops",
                )
            )

    def test_record_attempt_does_not_mark_game_captured(self) -> None:
        now = datetime(2026, 8, 5, 22, 0, tzinfo=timezone.utc)
        game = _game("game-1", now + timedelta(minutes=60))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture_registry.json"
            registry = CaptureRegistry(path)
            registry.record_attempt(
                games=[game],
                model_version="model-v1",
                line_source="playerprops",
                snapshot_path=None,
                captured_at=now,
                outcomes={"game-1": "failed"},
            )

            reloaded = CaptureRegistry(path)
            self.assertFalse(
                reloaded.is_captured(
                    game_id="game-1",
                    model_version="model-v1",
                    line_source="playerprops",
                )
            )

    def test_projection_id_is_deterministic_and_point_in_time_specific(self) -> None:
        projection = {
            "model_version": "model-v1",
            "game_id": "game-1",
            "player_name_norm": "test player",
            "prop_type": "PTS",
            "line": 17.5,
            "bookmaker": "fanduel",
            "line_collected_at": "2026-08-05T22:00:00+00:00",
        }

        self.assertEqual(projection_id(projection), projection_id(dict(projection)))
        later = dict(projection, line_collected_at="2026-08-05T22:30:00+00:00")
        self.assertNotEqual(projection_id(projection), projection_id(later))

    def test_game_is_too_late_when_any_line_is_collected_below_minimum_lead(self) -> None:
        game_time = datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        game = _game("game-1", game_time)
        lines = [
            PropLine(
                event_id="game-1",
                game_date=game.game_date,
                player_name_raw="Player A",
                player_name_norm="player a",
                team="NY",
                opponent="PHX",
                prop_type="PTS",
                line=10.5,
                bookmaker="fanduel",
                source="fixture",
                collected_at=game_time - timedelta(minutes=30),
            ),
            PropLine(
                event_id="game-1",
                game_date=game.game_date,
                player_name_raw="Player B",
                player_name_norm="player b",
                team="NY",
                opponent="PHX",
                prop_type="PTS",
                line=12.5,
                bookmaker="fanduel",
                source="fixture",
                collected_at=game_time - timedelta(minutes=19),
            ),
        ]

        self.assertTrue(_collected_too_late(game, lines, 20.0))


if __name__ == "__main__":
    unittest.main()
