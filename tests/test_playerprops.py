from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from wnba_props.config import Settings
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


if __name__ == "__main__":
    unittest.main()
