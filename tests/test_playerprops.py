from __future__ import annotations

import os
import unittest
from unittest import mock
from datetime import date, datetime, timezone

from wnba_props.config import (
    DEFAULT_PLAYERPROPS_BOOK_FALLBACKS,
    Settings,
    load_settings,
)
from wnba_props.models import Game
from wnba_props.sources.playerprops import PlayerPropsSource


class FixturePlayerPropsSource(PlayerPropsSource):
    def __init__(self, payload: dict) -> None:
        super().__init__(Settings(screen_date=date(2026, 8, 1)), lines_cache=None)  # type: ignore[arg-type]
        self.payload = payload

    def _fetch_payload(self) -> dict:
        return self.payload


def _game(game_id: str, home: str, away: str) -> Game:
    return Game(
        game_id=game_id,
        game_date=date(2026, 8, 1),
        game_time=datetime(2026, 8, 1, 23, 0, tzinfo=timezone.utc),
        home_team=home,
        away_team=away,
        source="espn",
    )


def _event(teams: list[str], player_name: str, team: str) -> dict:
    return {
        "teams": teams,
        "players": [
            {
                "playerName": player_name,
                "team": team,
                "stats": {
                    "Points": {
                        "plays": [
                            {
                                "source": "FANDUEL",
                                "line": 14.5,
                                "over": -115,
                                "under": -105,
                                "overDecimal": 1.87,
                                "underDecimal": 1.95,
                            }
                        ]
                    }
                },
            }
        ],
    }


def _play(source: str, line: float, over, under) -> dict:
    return {"source": source, "line": line, "over": over, "under": under}


def _plays_event(teams: list[str], player_name: str, team: str, plays: list[dict]) -> dict:
    return {
        "teams": teams,
        "players": [
            {
                "playerName": player_name,
                "team": team,
                "stats": {"Points": {"plays": plays}},
            }
        ],
    }


class PlayerPropsSourceTests(unittest.TestCase):
    def test_august_first_team_aliases_match_espn_slate(self) -> None:
        source = FixturePlayerPropsSource(
            {
                "eventPredictions": [
                    _event(["CHI", "LVA"], "Las Vegas Player", "LVA"),
                    _event(["NYL", "PHX"], "New York Player", "NYL"),
                ]
            }
        )

        lines = source.fetch_prop_lines(
            [
                _game("chi-lv", home="LV", away="CHI"),
                _game("ny-phx", home="PHX", away="NY"),
            ]
        )

        self.assertEqual(2, len(lines))
        self.assertEqual(
            {("LV", "CHI"), ("NY", "PHX")},
            {(line.team, line.opponent) for line in lines},
        )
        self.assertEqual(2, source.diagnostics["matched_events"])
        self.assertEqual([], source.diagnostics["unmatched_events"])
        self.assertEqual(-115, lines[0].over_odds)
        self.assertEqual(1.95, lines[0].under_decimal)

    def test_unmatched_events_are_reported(self) -> None:
        source = FixturePlayerPropsSource(
            {"eventPredictions": [_event(["CHI", "UNKNOWN"], "Unknown Player", "UNKNOWN")]}
        )

        lines = source.fetch_prop_lines([_game("chi-lv", home="LV", away="CHI")])

        self.assertEqual([], lines)
        self.assertEqual(1, len(source.diagnostics["unmatched_events"]))
        self.assertTrue(any("did not match the ESPN slate" in failure for failure in source.failures))


class AlternateBookCollectionTests(unittest.TestCase):
    """Same-line, two-sided alternate prices collected in configured order."""

    def _line(self, plays: list[dict]):
        source = FixturePlayerPropsSource(
            {"eventPredictions": [_plays_event(["CHI", "LV"], "Las Vegas Player", "LVA", plays)]}
        )
        lines = source.fetch_prop_lines([_game("chi-lv", home="LV", away="CHI")])
        self.assertEqual(1, len(lines))
        return lines[0], source

    def test_same_line_two_sided_alternates_are_collected_in_priority_order(self) -> None:
        line, source = self._line(
            [
                _play("FANDUEL", 14.5, -115, -105),
                _play("ESPN", 14.5, -108, -112),
                _play("CAESARS", 14.5, -110, None),  # one-sided -> rejected
                _play("HARDROCKBET", 15.5, -110, -110),  # different line -> rejected
                _play("DRAFTKINGS", 14.5, -120, 100),
                _play("BOVADA", 14.5, -105, -115),  # not configured -> ignored
            ]
        )

        # Primary configured-book quote is untouched and stays the model line.
        self.assertEqual("fanduel", line.bookmaker)
        self.assertEqual(14.5, line.line)
        self.assertEqual(-115, line.over_odds)
        self.assertEqual(-105, line.under_odds)
        self.assertEqual(
            ["draftkings", "espn"],
            [alternate.bookmaker for alternate in line.alternate_books],
        )
        self.assertEqual(
            [(-120, 100), (-108, -112)],
            [
                (alternate.over_odds, alternate.under_odds)
                for alternate in line.alternate_books
            ],
        )
        self.assertEqual(1, source.diagnostics["alternate_book_lines"])

    def test_payload_order_does_not_change_priority_order(self) -> None:
        configured = [
            _play("DRAFTKINGS", 14.5, -120, 100),
            _play("ESPN", 14.5, -108, -112),
        ]
        first, _ = self._line([_play("FANDUEL", 14.5, -115, -105)] + configured)
        second, _ = self._line(list(reversed(configured)) + [_play("FANDUEL", 14.5, -115, -105)])

        self.assertEqual(
            [alternate.bookmaker for alternate in first.alternate_books],
            [alternate.bookmaker for alternate in second.alternate_books],
        )
        self.assertEqual(
            [alternate.bookmaker for alternate in first.alternate_books],
            ["draftkings", "espn"],
        )

    def test_zero_or_missing_side_is_rejected(self) -> None:
        line, source = self._line(
            [
                _play("FANDUEL", 14.5, -115, -105),
                _play("DRAFTKINGS", 14.5, -120, 0),  # zero under -> rejected
                _play("CAESARS", 14.5, None, -110),  # missing over -> rejected
            ]
        )

        self.assertEqual([], line.alternate_books)
        self.assertEqual(0, source.diagnostics["alternate_book_lines"])

    def test_alternate_books_never_replace_the_primary_line(self) -> None:
        line, _ = self._line(
            [
                _play("FANDUEL", 14.5, -115, -105),
                _play("DRAFTKINGS", 16.5, 200, 200),
                _play("ESPN", 13.5, -150, 120),
            ]
        )

        self.assertEqual(14.5, line.line)
        self.assertEqual("fanduel", line.bookmaker)
        self.assertEqual([], line.alternate_books)

    def test_first_qualifying_play_per_book_is_used(self) -> None:
        line, _ = self._line(
            [
                _play("FANDUEL", 14.5, -115, -105),
                # Same book twice: the off-line play must not shadow the
                # same-line two-sided play.
                _play("DRAFTKINGS", 16.5, -110, -110),
                _play("DRAFTKINGS", 14.5, -120, 100),
                _play("CAESARS", 14.5, -110, None),
                _play("CAESARS", 14.5, -108, -112),
            ]
        )

        self.assertEqual(
            [("draftkings", -120, 100), ("caesars", -108, -112)],
            [
                (alternate.bookmaker, alternate.over_odds, alternate.under_odds)
                for alternate in line.alternate_books
            ],
        )

    def test_no_primary_book_play_yields_no_line(self) -> None:
        source = FixturePlayerPropsSource(
            {
                "eventPredictions": [
                    _plays_event(
                        ["CHI", "LV"],
                        "Las Vegas Player",
                        "LVA",
                        [_play("DRAFTKINGS", 14.5, -115, -105)],
                    )
                ]
            }
        )

        lines = source.fetch_prop_lines([_game("chi-lv", home="LV", away="CHI")])

        self.assertEqual([], lines)

    def test_default_payload_has_no_alternates(self) -> None:
        source = FixturePlayerPropsSource(
            {"eventPredictions": [_event(["CHI", "LVA"], "Las Vegas Player", "LVA")]}
        )

        lines = source.fetch_prop_lines([_game("chi-lv", home="LV", away="CHI")])

        self.assertEqual(1, len(lines))
        self.assertEqual([], lines[0].alternate_books)
        self.assertEqual(0, source.diagnostics["alternate_book_lines"])


class BookFallbackSettingsTests(unittest.TestCase):
    def test_default_fallback_priority(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAYERPROPS_BOOK_FALLBACKS", None)
            settings = load_settings()

        self.assertEqual(
            ["DRAFTKINGS", "CAESARS", "HARDROCKBET", "ESPN"],
            settings.playerprops_book_fallbacks,
        )
        self.assertEqual(
            DEFAULT_PLAYERPROPS_BOOK_FALLBACKS, settings.playerprops_book_fallbacks
        )

    def test_env_override_is_ordered_uppercased_and_deduped(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"PLAYERPROPS_BOOK_FALLBACKS": " caesars,draftkings ,ESPN,caesars"},
            clear=False,
        ):
            settings = load_settings()

        self.assertEqual(
            ["CAESARS", "DRAFTKINGS", "ESPN"], settings.playerprops_book_fallbacks
        )

    def test_blank_env_uses_default(self) -> None:
        with mock.patch.dict(
            os.environ, {"PLAYERPROPS_BOOK_FALLBACKS": "   "}, clear=False
        ):
            settings = load_settings()

        self.assertEqual(
            DEFAULT_PLAYERPROPS_BOOK_FALLBACKS, settings.playerprops_book_fallbacks
        )

    def test_none_disables_fallback(self) -> None:
        with mock.patch.dict(
            os.environ, {"PLAYERPROPS_BOOK_FALLBACKS": "none"}, clear=False
        ):
            settings = load_settings()

        self.assertEqual([], settings.playerprops_book_fallbacks)

    def test_dataclass_default_is_not_shared_between_instances(self) -> None:
        first = Settings()
        second = Settings()

        first.playerprops_book_fallbacks.append("BOVADA")

        self.assertEqual(
            ["DRAFTKINGS", "CAESARS", "HARDROCKBET", "ESPN"],
            second.playerprops_book_fallbacks,
        )


if __name__ == "__main__":
    unittest.main()
