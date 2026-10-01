from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import run_forecast_pipeline as pipeline
from wnba_props.cache import JsonCache
from wnba_props.models import Game, PlayerGameLog
from wnba_props.sources.espn import EspnSlateSource
from wnba_props.sources.espn_boxscore import EspnBoxscoreSource, EspnBoxscoreStatLine
from wnba_props.sources.espn_gamelog import EspnGameLogSource
from wnba_props.sources.espn_recent_boxscore_logs import (
    MAX_CONSECUTIVE_SLATE_FAILURES,
    EspnBoundedBoxscoreLogsSource,
)
from wnba_props.utils import normalize_name

SCREEN_DATE = date(2026, 9, 29)
COLLIER = "Napheesa Collier"
STEWART = "Breanna Stewart"


def _game(game_date: date, home: str = "NY", away: str = "MIN") -> Game:
    return Game(
        game_id=f"g-{game_date.isoformat()}",
        game_date=game_date,
        game_time=datetime(
            game_date.year, game_date.month, game_date.day, 23, 0, tzinfo=timezone.utc
        ),
        home_team=home,
        away_team=away,
        source="espn",
    )


def _stat_line(
    name: str,
    team: str,
    *,
    minutes: float = 30.0,
    points: int = 20,
    rebounds: int = 5,
    assists: int = 4,
    threes_made: int = 1,
) -> EspnBoxscoreStatLine:
    return EspnBoxscoreStatLine(
        player_name_raw=name,
        player_name_norm=normalize_name(name),
        team=team,
        minutes=minutes,
        points=points,
        rebounds=rebounds,
        assists=assists,
        threes_made=threes_made,
    )


def _log(
    day: str,
    *,
    minutes: float = 30.0,
    points: int = 20,
    team: str = "MIN",
    source: str = "basketball_reference",
    opponent: str = "NY",
) -> PlayerGameLog:
    return PlayerGameLog(
        player_name_raw=COLLIER,
        player_name_norm=normalize_name(COLLIER),
        game_date=date.fromisoformat(day),
        team=team,
        opponent=opponent,
        minutes=minutes,
        points=points,
        rebounds=5,
        assists=4,
        threes_made=1,
        did_play=True,
        source=source,
    )


def _settings(screen: str = "2026-09-29", phase: str = "playoff") -> SimpleNamespace:
    return SimpleNamespace(
        screen_date=date.fromisoformat(screen),
        season_phase=phase,
        sticky_daily_log_cache=True,
    )


def _line(name: str = COLLIER, team: str = "MIN") -> SimpleNamespace:
    return SimpleNamespace(
        player_name_raw=name,
        player_name_norm=normalize_name(name),
        team=team,
    )


class BoundedBoxscoreWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = JsonCache(Path(self._tmp.name), ttl_hours=24)
        self.slate_calls: list[date] = []
        self.boxscore_calls: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _slate(self, game_date: date) -> list[Game]:
        self.slate_calls.append(game_date)
        return [_game(game_date)]

    def _boxscore(self, event_id: str):
        self.boxscore_calls.append(event_id)
        return {
            (normalize_name(COLLIER), "MIN"): _stat_line(COLLIER, "MIN"),
            (normalize_name(STEWART), "NY"): _stat_line(STEWART, "NY", minutes=38.0, points=34),
        }

    def test_each_window_date_is_fetched_once_and_shared_by_players(self) -> None:
        source = EspnBoundedBoxscoreLogsSource(self.cache, lookback_days=5, max_games=5)
        with (
            patch.object(EspnSlateSource, "fetch_games", side_effect=self._slate),
            patch.object(EspnBoxscoreSource, "fetch_boxscore", side_effect=self._boxscore),
        ):
            first = source.fetch_logs(COLLIER, "MIN", SCREEN_DATE)
            calls_after_first_player = len(self.slate_calls)
            window_after_first_player = (source.window_start, source.window_end)
            second = source.fetch_logs(STEWART, "NY", SCREEN_DATE)
            source.fetch_logs(COLLIER, "MIN", SCREEN_DATE.replace(day=30))

        self.assertEqual(calls_after_first_player, 5)
        self.assertEqual(window_after_first_player, (date(2026, 9, 24), date(2026, 9, 28)))
        # A second player reuses the same window, and the next screen date only
        # pays for the one date the window gained.
        self.assertEqual(len(self.slate_calls), 6)
        self.assertEqual(
            [log.game_date.isoformat() for log in first],
            ["2026-09-28", "2026-09-27", "2026-09-26", "2026-09-25", "2026-09-24"],
        )
        self.assertEqual([log.game_date for log in second], [log.game_date for log in first])
        self.assertEqual(source.dates_with_games, 5)
        self.assertEqual(source.slate_failures, 0)

    def test_window_is_bounded_by_lookback_days(self) -> None:
        source = EspnBoundedBoxscoreLogsSource(self.cache, lookback_days=3)
        with (
            patch.object(EspnSlateSource, "fetch_games", side_effect=self._slate),
            patch.object(EspnBoxscoreSource, "fetch_boxscore", side_effect=self._boxscore),
        ):
            logs = source.fetch_logs(COLLIER, "MIN", SCREEN_DATE)

        self.assertEqual(self.slate_calls, [date(2026, 9, 28), date(2026, 9, 27), date(2026, 9, 26)])
        self.assertEqual(source.window_start, date(2026, 9, 26))
        self.assertEqual(
            [log.game_date.isoformat() for log in logs],
            ["2026-09-28", "2026-09-27", "2026-09-26"],
        )

    def test_never_uses_a_game_on_or_after_the_screen_date(self) -> None:
        source = EspnBoundedBoxscoreLogsSource(self.cache, lookback_days=4)
        with (
            patch.object(EspnSlateSource, "fetch_games", side_effect=self._slate),
            patch.object(EspnBoxscoreSource, "fetch_boxscore", side_effect=self._boxscore),
        ):
            logs = source.fetch_logs(COLLIER, "MIN", SCREEN_DATE)

        self.assertTrue(all(day < SCREEN_DATE for day in self.slate_calls))
        self.assertTrue(all(log.game_date < SCREEN_DATE for log in logs))
        self.assertNotIn(f"g-{SCREEN_DATE.isoformat()}", self.boxscore_calls)

    def test_missing_boxscore_and_dnp_add_no_rows(self) -> None:
        blank = "Deep Bench"
        absent = "Absent Player"

        def boxscore(event_id: str):
            return {
                (normalize_name(COLLIER), "MIN"): _stat_line(COLLIER, "MIN"),
                (normalize_name(blank), "MIN"): _stat_line(
                    blank, "MIN", minutes=0.0, points=0, rebounds=0, assists=0, threes_made=0
                ),
            }

        source = EspnBoundedBoxscoreLogsSource(self.cache, lookback_days=2)
        with (
            patch.object(EspnSlateSource, "fetch_games", side_effect=self._slate),
            patch.object(EspnBoxscoreSource, "fetch_boxscore", side_effect=boxscore),
        ):
            played = source.fetch_logs(COLLIER, "MIN", SCREEN_DATE)
            dnp = source.fetch_logs(blank, "MIN", SCREEN_DATE)
            missing = source.fetch_logs(absent, "MIN", SCREEN_DATE)

        self.assertEqual(len(played), 2)
        self.assertEqual(dnp, [])
        self.assertEqual(missing, [])

    def test_a_boxscore_failure_does_not_abort_the_window(self) -> None:
        def boxscore(event_id: str):
            if event_id.endswith("2026-09-27"):
                raise RuntimeError("HTTP 500 for ESPN")
            return {(normalize_name(COLLIER), "MIN"): _stat_line(COLLIER, "MIN")}

        source = EspnBoundedBoxscoreLogsSource(self.cache, lookback_days=3)
        with (
            patch.object(EspnSlateSource, "fetch_games", side_effect=self._slate),
            patch.object(EspnBoxscoreSource, "fetch_boxscore", side_effect=boxscore),
        ):
            logs = source.fetch_logs(COLLIER, "MIN", SCREEN_DATE)

        self.assertEqual(
            [log.game_date.isoformat() for log in logs], ["2026-09-28", "2026-09-26"]
        )

    def test_scan_stops_after_repeated_slate_failures(self) -> None:
        def failing_slate(game_date: date) -> list[Game]:
            self.slate_calls.append(game_date)
            raise RuntimeError("HTTP 403 for ESPN")

        source = EspnBoundedBoxscoreLogsSource(self.cache, lookback_days=10)
        with (
            patch.object(EspnSlateSource, "fetch_games", side_effect=failing_slate),
            patch.object(EspnBoxscoreSource, "fetch_boxscore", side_effect=self._boxscore),
        ):
            logs = source.fetch_logs(COLLIER, "MIN", SCREEN_DATE)

        self.assertEqual(logs, [])
        self.assertEqual(len(self.slate_calls), MAX_CONSECUTIVE_SLATE_FAILURES)
        self.assertEqual(source.slate_failures, MAX_CONSECUTIVE_SLATE_FAILURES)


class LogStalenessRuleTests(unittest.TestCase):
    def test_playoff_boundary_is_two_days(self) -> None:
        fresh = [_log("2026-09-27")]
        self.assertFalse(pipeline._player_logs_stale(SCREEN_DATE, fresh, phase="playoff"))
        self.assertTrue(
            pipeline._player_logs_stale(SCREEN_DATE, [_log("2026-09-26")], phase="playoff")
        )
        self.assertTrue(pipeline._player_logs_stale(SCREEN_DATE, [], phase="playoff"))

    def test_non_playoff_phase_never_flags_staleness(self) -> None:
        self.assertFalse(
            pipeline._player_logs_stale(SCREEN_DATE, [_log("2026-09-01")], phase="regular")
        )
        self.assertFalse(pipeline._player_logs_stale(SCREEN_DATE, [], phase="regular"))


class MergeTests(unittest.TestCase):
    def test_primary_wins_and_extra_dates_are_added(self) -> None:
        primary = [_log("2026-09-24", minutes=31.0), _log("2026-09-20")]
        extra = [_log("2026-09-27", minutes=33.0, source="espn_boxscore_logs"), _log("2026-09-24", minutes=99.0)]

        merged = pipeline._merge_player_logs(primary, extra)

        self.assertEqual(
            [log.game_date.isoformat() for log in merged],
            ["2026-09-27", "2026-09-24", "2026-09-20"],
        )
        by_date = {log.game_date.isoformat(): log for log in merged}
        self.assertEqual(by_date["2026-09-24"].minutes, 31.0)
        self.assertEqual(by_date["2026-09-24"].source, "basketball_reference")
        self.assertEqual(by_date["2026-09-27"].source, "espn_boxscore_logs")

    def test_merge_does_not_mutate_inputs(self) -> None:
        primary = [_log("2026-09-20")]
        extra = [_log("2026-09-27")]
        pipeline._merge_player_logs(primary, extra)
        self.assertEqual(len(primary), 1)
        self.assertEqual(len(extra), 1)


class CollectPlayerLogsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.shared_cache = JsonCache(Path(self._tmp.name), ttl_hours=24)
        self.mocks: dict[str, MagicMock] = {}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _returning(payload):
        outer = payload
        return lambda *args, **kwargs: list(outer) if isinstance(outer, list) else outer

    def _collect(
        self,
        *,
        settings,
        bref_logs=None,
        bref_error=None,
        gamelog_error=None,
        boxscore_logs=None,
        boxscore_error=None,
    ):
        def boxscore_side_effect(player_name, team_abbr, screen_date):
            if boxscore_error is not None:
                raise boxscore_error
            return list(boxscore_logs or [])

        def raiser(error):
            def _raise(*args, **kwargs):
                raise error

            return _raise

        self.mocks = {
            "bref": MagicMock(
                side_effect=raiser(bref_error)
                if bref_error is not None
                else self._returning(list(bref_logs or []))
            ),
            "gamelog": MagicMock(
                side_effect=raiser(gamelog_error)
                if gamelog_error is not None
                else self._returning([])
            ),
            "boxscore": MagicMock(side_effect=boxscore_side_effect),
        }
        with (
            patch.object(
                pipeline.BasketballReferenceSource,
                "fetch_logs",
                self.mocks["bref"],
            ),
            patch.object(
                EspnGameLogSource, "fetch_logs", self.mocks["gamelog"]
            ),
            patch.object(
                EspnBoundedBoxscoreLogsSource,
                "fetch_logs",
                self.mocks["boxscore"],
            ),
        ):
            return pipeline._collect_player_logs(
                settings, [], [_line()], self.shared_cache, self.shared_cache
            )

    def test_waf_failure_tops_up_history_from_boxscores(self) -> None:
        logs_by_player, _statuses, _injuries, freshness = self._collect(
            settings=_settings(),
            bref_logs=[_log("2026-09-24"), _log("2026-09-20")],
            gamelog_error=RuntimeError(f"ESPN gamelog blocked by WAF challenge for {COLLIER}."),
            boxscore_logs=[_log("2026-09-27", minutes=33.0, source="espn_boxscore_logs"), _log("2026-09-24", minutes=99.0, source="espn_boxscore_logs")],
        )

        logs = logs_by_player[normalize_name(COLLIER)]
        self.assertEqual(
            [log.game_date.isoformat() for log in logs],
            ["2026-09-27", "2026-09-24", "2026-09-20"],
        )
        self.assertEqual(len(logs), 3)
        self.assertEqual(freshness["players_requested"], 1)
        self.assertEqual(freshness["players_with_logs"], 1)
        self.assertEqual(freshness["players_without_logs"], [])
        self.assertEqual(freshness["latest_log_date"], "2026-09-27")
        self.assertEqual(freshness["expected_recent_after"], "2026-09-27")
        self.assertEqual(freshness["stale_players"], [])
        self.assertEqual(freshness["boxscore_fallback"]["players_used"], 1)
        self.assertEqual(freshness["boxscore_fallback"]["added_games"], 1)
        self.assertEqual(
            freshness["boxscore_fallback"]["players"],
            [
                {
                    "player": normalize_name(COLLIER),
                    "team": "MIN",
                    "added_games": 1,
                    "latest_log_date": "2026-09-27",
                }
            ],
        )

    def test_fresh_playoff_log_skips_both_fallbacks(self) -> None:
        _logs, _s, _i, freshness = self._collect(
            settings=_settings(),
            bref_logs=[_log("2026-09-27")],
            boxscore_logs=[_log("2026-09-27", source="espn_boxscore_logs")],
        )

        self.assertEqual(self.mocks["boxscore"].call_count, 0)
        self.assertEqual(self.mocks["gamelog"].call_count, 0)
        self.assertEqual(freshness["boxscore_fallback"]["players_used"], 0)
        self.assertEqual(freshness["stale_players"], [])
        self.assertEqual(freshness["latest_log_date"], "2026-09-27")

    def test_regular_season_phase_does_not_call_the_fallback(self) -> None:
        _logs, _s, _i, freshness = self._collect(
            settings=_settings(phase="regular"),
            bref_logs=[_log("2026-09-21")],
            boxscore_logs=[_log("2026-09-27", source="espn_boxscore_logs")],
        )

        self.assertEqual(self.mocks["boxscore"].call_count, 0)
        self.assertEqual(freshness["stale_players"], [])
        self.assertEqual(freshness["latest_log_date"], "2026-09-21")

    def test_stale_player_survives_with_history_when_every_fallback_fails(self) -> None:
        logs_by_player, _s, _i, freshness = self._collect(
            settings=_settings(),
            bref_logs=[_log("2026-09-21")],
            gamelog_error=RuntimeError(f"ESPN gamelog blocked by WAF challenge for {COLLIER}."),
            boxscore_error=RuntimeError("HTTP 403 for ESPN"),
        )

        logs = logs_by_player[normalize_name(COLLIER)]
        self.assertEqual([log.game_date.isoformat() for log in logs], ["2026-09-21"])
        self.assertEqual(freshness["players_with_logs"], 1)
        self.assertEqual(freshness["latest_log_date"], "2026-09-21")
        self.assertEqual(
            freshness["stale_players"],
            [
                {
                    "player": normalize_name(COLLIER),
                    "team": "MIN",
                    "latest_log_date": "2026-09-21",
                }
            ],
        )
        self.assertEqual(freshness["boxscore_fallback"]["players_used"], 0)

    def test_player_with_no_logs_at_all_is_reported(self) -> None:
        logs_by_player, _s, _i, freshness = self._collect(
            settings=_settings(),
            bref_logs=[],
            bref_error=RuntimeError("HTTP 500 for bref"),
            gamelog_error=RuntimeError("no gamelog"),
            boxscore_error=RuntimeError("no boxscore"),
        )

        self.assertEqual(logs_by_player, {})
        self.assertEqual(freshness["players_without_logs"], [normalize_name(COLLIER)])
        self.assertEqual(freshness["latest_log_date"], None)
        self.assertEqual(
            freshness["stale_players"],
            [
                {
                    "player": normalize_name(COLLIER),
                    "team": "MIN",
                    "latest_log_date": None,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
