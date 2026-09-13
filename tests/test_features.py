from __future__ import annotations

import unittest
from datetime import date, timedelta

from wnba_props.features.player import (
    build_player_features,
    build_player_feature_table,
    league_stat_baselines,
)
from wnba_props.features.team import (
    TeamGameResult,
    build_team_features,
    parse_team_results_from_scoreboard,
)
from wnba_props.models import PlayerGameLog


def _log(
    *,
    player: str = "test player",
    day: int,
    team: str = "NY",
    opponent: str = "PHX",
    minutes: float = 30.0,
    points: int = 20,
    rebounds: int = 5,
    assists: int = 4,
    threes: int = 2,
) -> PlayerGameLog:
    return PlayerGameLog(
        player_name_raw=player.title(),
        player_name_norm=player,
        game_date=date(2026, 6, 1) + timedelta(days=day),
        team=team,
        opponent=opponent,
        minutes=minutes,
        points=points,
        rebounds=rebounds,
        assists=assists,
        threes_made=threes,
        did_play=True,
        source="fixture",
    )


class TeamFeatureTests(unittest.TestCase):
    def test_point_in_time_excludes_future_games(self) -> None:
        results = [
            TeamGameResult(date(2026, 6, 1), "NY", "PHX", 90, 80, home=True),
            TeamGameResult(date(2026, 6, 3), "NY", "PHX", 70, 75, home=True),
            TeamGameResult(date(2026, 6, 5), "NY", "PHX", 100, 60, home=True),
        ]
        features = build_team_features(
            team="NY",
            opponent="PHX",
            game_date=date(2026, 6, 4),
            results=results,
            is_home=True,
        )
        self.assertIsNotNone(features)
        assert features is not None
        self.assertEqual(features.games_played, 2)
        self.assertAlmostEqual(features.ppg_last_5, 80.0)
        self.assertEqual(features.rest_days, 1)

    def test_no_prior_games_returns_none(self) -> None:
        features = build_team_features(
            team="NY",
            opponent="PHX",
            game_date=date(2026, 6, 1),
            results=[TeamGameResult(date(2026, 6, 1), "NY", "PHX", 90, 80, home=True)],
            is_home=True,
        )
        self.assertIsNone(features)

    def test_parse_scoreboard_final_games(self) -> None:
        payload = {
            "events": [
                {
                    "status": {"type": {"completed": True}},
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "score": "88",
                                    "team": {"displayName": "Indiana Fever", "abbreviation": "IND"},
                                },
                                {
                                    "homeAway": "away",
                                    "score": "81",
                                    "team": {"displayName": "New York Liberty", "abbreviation": "NY"},
                                },
                            ]
                        }
                    ],
                }
            ]
        }
        results = parse_team_results_from_scoreboard(
            payload,
            game_date=date(2026, 6, 1),
            team_map={"Indiana Fever": "IND", "New York Liberty": "NY"},
        )
        self.assertEqual(len(results), 2)
        home = [r for r in results if r.home][0]
        self.assertEqual(home.team, "IND")
        self.assertEqual(home.team_score, 88)
        self.assertEqual(home.opponent_score, 81)

    def test_parse_scoreboard_skips_incomplete(self) -> None:
        payload = {
            "events": [
                {
                    "status": {"type": {"completed": False}},
                    "competitions": [
                        {"competitors": [{"homeAway": "home", "score": "10", "team": {}}]}
                    ],
                }
            ]
        }
        results = parse_team_results_from_scoreboard(
            payload, game_date=date(2026, 6, 1), team_map={}
        )
        self.assertEqual(results, [])


class PlayerFeatureTests(unittest.TestCase):
    def test_rates_and_point_in_time(self) -> None:
        logs = [_log(day=index, points=10 + index) for index in range(12)]
        features = build_player_features(
            logs=logs,
            league_logs=logs,
            player_name_norm="test player",
            player_name_raw="Test Player",
            team="NY",
            opponent="PHX",
            game_date=date(2026, 6, 13),
            prop_type="PTS",
        )
        self.assertIsNotNone(features)
        assert features is not None
        self.assertEqual(features.games_played, 12)
        self.assertGreater(features.rate_season, 0.0)
        self.assertEqual(features.prop_type, "PTS")

    def test_minimum_games_gate(self) -> None:
        logs = [_log(day=index) for index in range(3)]
        features = build_player_features(
            logs=logs,
            league_logs=logs,
            player_name_norm="test player",
            player_name_raw="Test Player",
            team="NY",
            opponent="PHX",
            game_date=date(2026, 6, 10),
            prop_type="PTS",
        )
        self.assertIsNone(features)

    def test_feature_table_has_no_leakage(self) -> None:
        logs = [_log(day=index, points=5 * index) for index in range(8)]
        rows = build_player_feature_table(logs=logs, prop_types=["PTS"], minimum_games=5)
        for row in rows:
            prior = [
                log for log in logs if log.game_date < row.game_date
            ]
            self.assertEqual(row.games_played, len(prior))

    def test_league_baseline_is_per_team_game(self) -> None:
        logs = [
            _log(player="a", day=0, team="NY", points=20),
            _log(player="b", day=0, team="NY", points=10),
            _log(player="a", day=1, team="NY", points=30),
            _log(player="b", day=1, team="NY", points=10),
        ]
        baselines = league_stat_baselines(logs, ["PTS"])
        self.assertAlmostEqual(baselines["PTS"], 35.0)


if __name__ == "__main__":
    unittest.main()
