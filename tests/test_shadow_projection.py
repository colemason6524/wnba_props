from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from wnba_props.models import PlayerGameLog, PropLine
from wnba_props.shadow.pricing import expected_profit_units, implied_probability
from wnba_props.shadow.projection import ProjectionConfig, project_points_line
from wnba_props.shadow.sources import ShadowEspnSlateSource


SCREEN_DATE = date(2026, 8, 4)


def _line(*, line_value: float = 17.5, over: int | None = -110, under: int | None = -110) -> PropLine:
    return PropLine(
        event_id="game-1",
        game_date=SCREEN_DATE,
        player_name_raw="Test Player",
        player_name_norm="test player",
        team="NY",
        opponent="PHX",
        prop_type="PTS",
        line=line_value,
        bookmaker="fanduel",
        source="fixture",
        collected_at=datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc),
        over_odds=over,
        under_odds=under,
    )


def _logs(count: int = 12) -> list[PlayerGameLog]:
    logs = []
    for index in range(count):
        logs.append(
            PlayerGameLog(
                player_name_raw="Test Player",
                player_name_norm="test player",
                game_date=SCREEN_DATE - timedelta(days=index + 1),
                team="NY",
                opponent="PHX",
                minutes=30.0 + (index % 3),
                points=18 + (index % 4),
                rebounds=5,
                assists=4,
                threes_made=2,
                did_play=True,
                source="fixture",
            )
        )
    return logs


class ShadowProjectionTests(unittest.TestCase):
    def test_projection_is_deterministic(self) -> None:
        kwargs = {
            "line": _line(),
            "game_time": datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
            "logs": _logs(),
            "screen_date": SCREEN_DATE,
            "team_spread": -3.5,
            "game_total": 166.5,
            "config": ProjectionConfig(simulations=2_000),
        }

        first = project_points_line(**kwargs)
        second = project_points_line(**kwargs)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.projected_mean, second.projected_mean)  # type: ignore[union-attr]
        self.assertEqual(first.over_probability, second.over_probability)  # type: ignore[union-attr]

    def test_same_day_and_future_logs_are_excluded(self) -> None:
        logs = _logs(5)
        logs.extend(
            [
                PlayerGameLog(
                    player_name_raw="Test Player",
                    player_name_norm="test player",
                    game_date=SCREEN_DATE,
                    team="NY",
                    opponent="PHX",
                    minutes=40.0,
                    points=100,
                    rebounds=0,
                    assists=0,
                    threes_made=0,
                    did_play=True,
                    source="future",
                ),
                PlayerGameLog(
                    player_name_raw="Test Player",
                    player_name_norm="test player",
                    game_date=SCREEN_DATE + timedelta(days=1),
                    team="NY",
                    opponent="PHX",
                    minutes=40.0,
                    points=100,
                    rebounds=0,
                    assists=0,
                    threes_made=0,
                    did_play=True,
                    source="future",
                ),
            ]
        )

        projection = project_points_line(
            line=_line(),
            game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
            logs=logs,
            screen_date=SCREEN_DATE,
            config=ProjectionConfig(simulations=500),
        )

        self.assertIsNotNone(projection)
        self.assertEqual(5, projection.games_used)  # type: ignore[union-attr]
        self.assertLess(projection.projected_mean, 30.0)  # type: ignore[union-attr]

    def test_missing_prices_never_create_a_wager_decision(self) -> None:
        projection = project_points_line(
            line=_line(over=None, under=None),
            game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
            logs=_logs(),
            screen_date=SCREEN_DATE,
            config=ProjectionConfig(simulations=500),
        )

        self.assertIsNotNone(projection)
        self.assertEqual("NO_PRICE", projection.price_status)  # type: ignore[union-attr]
        self.assertEqual("RESEARCH_ONLY", projection.decision)  # type: ignore[union-attr]
        self.assertIsNone(projection.over_expected_value)  # type: ignore[union-attr]
        self.assertIn("SPREAD_MISSING", projection.flags)  # type: ignore[union-attr]
        self.assertIn("GAME_TOTAL_MISSING", projection.flags)  # type: ignore[union-attr]

    def test_price_can_reverse_expected_value_at_same_probability(self) -> None:
        self.assertAlmostEqual(0.5238, implied_probability(-110) or 0.0, places=4)
        ev_at_110 = expected_profit_units(0.55, 0.45, -110)
        ev_at_130 = expected_profit_units(0.55, 0.45, -130)

        self.assertGreater(ev_at_110 or 0.0, 0.0)
        self.assertLess(ev_at_130 or 0.0, 0.0)

    def test_out_player_is_not_projected(self) -> None:
        projection = project_points_line(
            line=_line(),
            game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
            logs=_logs(),
            screen_date=SCREEN_DATE,
            player_status="Out",
            config=ProjectionConfig(simulations=500),
        )

        self.assertIsNone(projection)

    def test_shadow_slate_parser_is_isolated_and_normalizes_teams(self) -> None:
        games = ShadowEspnSlateSource.parse_games(
            {
                "events": [
                    {
                        "id": "game-1",
                        "date": "2026-08-04T23:00:00Z",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {"displayName": "New York Liberty"},
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {"displayName": "Phoenix Mercury"},
                                    },
                                ],
                                "odds": [
                                    {
                                        "provider": {"name": "Fixture Book"},
                                        "overUnder": 169.5,
                                        "spread": -11.5,
                                        "pointSpread": {
                                            "home": {"close": {"line": "-11.5"}},
                                            "away": {"close": {"line": "+11.5"}},
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            SCREEN_DATE,
        )

        self.assertEqual(1, len(games))
        self.assertEqual("NY", games[0].home_team)
        self.assertEqual("PHX", games[0].away_team)
        self.assertEqual("espn_shadow", games[0].source)

        contexts = ShadowEspnSlateSource.parse_game_contexts(
            {
                "events": [
                    {
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {"displayName": "New York Liberty"},
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {"displayName": "Phoenix Mercury"},
                                    },
                                ],
                                "odds": [
                                    {
                                        "provider": {"name": "Fixture Book"},
                                        "overUnder": 169.5,
                                        "spread": -11.5,
                                        "pointSpread": {
                                            "home": {"close": {"line": "-11.5"}},
                                            "away": {"close": {"line": "+11.5"}},
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        )
        context = contexts[("PHX", "NY")]
        self.assertEqual(-11.5, context.home_spread)
        self.assertEqual(11.5, context.away_spread)
        self.assertEqual(169.5, context.total)
        self.assertEqual("Fixture Book", context.provider)


if __name__ == "__main__":
    unittest.main()
