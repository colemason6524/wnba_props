from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from wnba_props.models import PlayerGameLog, PropLine
from wnba_props.shadow.calibration import load_calibration_artifact
from wnba_props.shadow.pricing import (
    expected_profit_units,
    implied_probability,
    is_valid_price,
    normalize_odds,
)
from wnba_props.shadow.projection import (
    ProjectionConfig,
    _model_side,
    config_signature,
    project_points_line,
)
from wnba_props.shadow.sources import ShadowEspnSlateSource


SCREEN_DATE = date(2026, 8, 4)

FIXTURE_PAIRS = [
    [0.0, 0.0],
    [1.0, 0.02],
    [-1.0, -0.02],
    [0.5, 0.01],
    [-0.5, -0.01],
]


def _write_artifact(directory: Path, *, pairs: list | None = None):
    payload = {
        "schema_version": 1,
        "residual_model_id": "v1-joint-residual-fixture",
        "source_model_version": "wnba-points-shadow-v1",
        "source_config_hash": "b3096ccb93b6f4d1",
        "source_commit": "3184273954dd0eaba7966d8a791093633fbf3edf",
        "n_rows": len(pairs if pairs is not None else FIXTURE_PAIRS),
        "projection_ids_sha256": "0" * 64,
        "pairs": pairs if pairs is not None else FIXTURE_PAIRS,
    }
    path = directory / "fixture_artifact.json"
    path.write_text(json.dumps(payload))
    return load_calibration_artifact(path)


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
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            kwargs = {
                "line": _line(),
                "game_time": datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                "logs": _logs(),
                "screen_date": SCREEN_DATE,
                "team_spread": -3.5,
                "game_total": 166.5,
                "config": ProjectionConfig(simulations=2_000),
                "residuals": artifact,
            }

            first = project_points_line(**kwargs)
            second = project_points_line(**kwargs)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first.projected_mean, second.projected_mean)
            self.assertEqual(first.over_probability, second.over_probability)

    def test_different_artifact_changes_the_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=2_000),
                residuals=_write_artifact(root),
            )
            wide_pairs = [[value * 3.0, rate * 3.0] for value, rate in FIXTURE_PAIRS]
            second = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=2_000),
                residuals=_write_artifact(root, pairs=wide_pairs),
            )

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first.percentile_10, second.percentile_10)

    def test_missing_residuals_raise(self) -> None:
        with self.assertRaises(ValueError):
            project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=500),
            )

    def test_calibration_lambda_zero_forces_fifty_fifty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            uncalibrated = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=2_000),
                residuals=artifact,
            )
            calibrated = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=2_000, calibration_lambda=0.0),
                residuals=artifact,
            )

            self.assertIsNotNone(uncalibrated)
            self.assertIsNotNone(calibrated)
            self.assertAlmostEqual(
                uncalibrated.raw_conditional_over_probability,
                calibrated.raw_conditional_over_probability,
                places=4,
            )
            self.assertAlmostEqual(calibrated.conditional_over_probability, 0.5, places=4)
            self.assertAlmostEqual(calibrated.calibration_lambda, 0.0, places=4)
            self.assertGreater(calibrated.raw_conditional_over_probability, 0.0)

    def test_calibration_lambda_one_matches_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            calibrated = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=2_000, calibration_lambda=1.0),
                residuals=artifact,
            )

            self.assertIsNotNone(calibrated)
            self.assertAlmostEqual(
                calibrated.conditional_over_probability,
                calibrated.raw_conditional_over_probability,
                places=4,
            )

    def test_probability_mass_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            projection = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=2_000, calibration_lambda=0.7),
                residuals=artifact,
            )

            self.assertIsNotNone(projection)
            total = (
                projection.over_probability
                + projection.under_probability
                + projection.push_probability
            )
            self.assertAlmostEqual(total, 1.0, places=3)

    def test_v1_central_formulas_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            projection = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=500),
                residuals=artifact,
            )

            # minutes pattern 30,31,32 x4 -> season average 31.0
            self.assertEqual(31.0, projection.season_minutes_avg)
            # points pattern 18,19,20,21 x3 -> 234 points / 372 minutes
            self.assertAlmostEqual(projection.season_points_per_minute, 0.629, places=3)

    def test_one_sided_price_never_selects_a_side(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            projection = project_points_line(
                line=_line(over=None),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=500),
                residuals=artifact,
            )

            self.assertEqual("ONE_SIDE_PRICED", projection.price_status)
            self.assertEqual("PASS", projection.model_side)
            self.assertIn("PRICE_INCOMPLETE", projection.flags)

    def test_model_side_requires_positive_unique_expected_value(self) -> None:
        self.assertEqual("PASS", _model_side(over_expected_value=None, under_expected_value=None, price_status="NO_PRICE"))
        self.assertEqual("PASS", _model_side(over_expected_value=0.10, under_expected_value=-0.05, price_status="ONE_SIDE_PRICED"))
        self.assertEqual("PASS", _model_side(over_expected_value=-0.10, under_expected_value=-0.05, price_status="BOTH_SIDES_PRICED"))
        self.assertEqual("PASS", _model_side(over_expected_value=0.07, under_expected_value=0.07, price_status="BOTH_SIDES_PRICED"))
        self.assertEqual("OVER", _model_side(over_expected_value=0.08, under_expected_value=0.02, price_status="BOTH_SIDES_PRICED"))
        self.assertEqual("UNDER", _model_side(over_expected_value=-0.01, under_expected_value=0.09, price_status="BOTH_SIDES_PRICED"))

    def test_out_player_is_not_projected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            projection = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                player_status="Out",
                config=ProjectionConfig(simulations=500),
                residuals=artifact,
            )

            self.assertIsNone(projection)

    def test_availability_status_is_normalized_and_stored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            projection = project_points_line(
                line=_line(),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                player_status="Questionable",
                config=ProjectionConfig(simulations=500),
                residuals=artifact,
                injury_source_available=False,
            )

            self.assertEqual("questionable", projection.availability_status)
            self.assertFalse(projection.injury_source_available)
            self.assertIn("AVAILABILITY_UNCERTAIN", projection.flags)

    def test_same_day_and_future_logs_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
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
                residuals=artifact,
            )

            self.assertIsNotNone(projection)
            self.assertEqual(5, projection.games_used)
            self.assertLess(projection.projected_mean, 30.0)

    def test_missing_prices_never_create_a_wager_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = _write_artifact(Path(directory))
            projection = project_points_line(
                line=_line(over=None, under=None),
                game_time=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
                logs=_logs(),
                screen_date=SCREEN_DATE,
                config=ProjectionConfig(simulations=500),
                residuals=artifact,
            )

            self.assertIsNotNone(projection)
            self.assertEqual("NO_PRICE", projection.price_status)
            self.assertEqual("RESEARCH_ONLY", projection.decision)
            self.assertIsNone(projection.over_expected_value)
            self.assertIn("SPREAD_MISSING", projection.flags)
            self.assertIn("GAME_TOTAL_MISSING", projection.flags)

    def test_price_can_reverse_expected_value_at_same_probability(self) -> None:
        self.assertAlmostEqual(0.5238, implied_probability(-110) or 0.0, places=4)
        ev_at_110 = expected_profit_units(0.55, 0.45, -110)
        ev_at_130 = expected_profit_units(0.55, 0.45, -130)

        self.assertGreater(ev_at_110 or 0.0, 0.0)
        self.assertLess(ev_at_130 or 0.0, 0.0)

    def test_config_signature_is_deterministic_sensitive_and_complete(self) -> None:
        self.assertEqual(config_signature(ProjectionConfig()), config_signature(ProjectionConfig()))
        self.assertNotEqual(
            config_signature(ProjectionConfig(simulations=10_000)),
            config_signature(ProjectionConfig(simulations=1_000)),
        )
        self.assertNotEqual(
            config_signature(ProjectionConfig()),
            config_signature(ProjectionConfig(calibration_lambda=0.85)),
        )

    def test_price_validation_helpers(self) -> None:
        self.assertTrue(is_valid_price(-110))
        self.assertTrue(is_valid_price(150))
        self.assertFalse(is_valid_price(0))
        self.assertFalse(is_valid_price(None))
        self.assertEqual(-110, normalize_odds(-110))
        self.assertEqual(150, normalize_odds("150"))
        self.assertIsNone(normalize_odds(0))
        self.assertIsNone(normalize_odds(None))

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
