from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from wnba_props.features.player import PlayerFeatures
from wnba_props.models import PropLine
from wnba_props.modeling.calibration import (
    ResidualArtifact,
    calibrate_probability,
    fit_calibration_lambda,
    load_residual_artifact,
    save_residual_artifact,
)
from wnba_props.modeling.minutes import dnp_probability, project_minutes, simulate_prop
from wnba_props.modeling.rates import game_environment_factor, project_rate
from wnba_props.features.positions import position_class
from wnba_props.modeling.value import (
    expected_value,
    expected_value_with_push,
    value_label,
)


def _features(**overrides) -> PlayerFeatures:
    base = dict(
        player_name_raw="Test Player",
        player_name_norm="test player",
        team="NY",
        opponent="PHX",
        game_date=date(2026, 6, 10),
        prop_type="PTS",
        games_played=12,
        minutes_avg_l5=30.0,
        minutes_avg_l10=29.0,
        minutes_avg_season=28.0,
        minutes_recency_weighted=30.0,
        minutes_sd_l10=4.0,
        rate_l5=0.70,
        rate_l10=0.68,
        rate_season=0.65,
        rate_recency_weighted=0.69,
        rate_sd=0.10,
        trend=0.05,
        games_last_7_days=3,
        games_last_14_days=6,
        opponent_allowance=78.0,
        starting=True,
    )
    base.update(overrides)
    return PlayerFeatures(**base)


def _artifact(**overrides) -> ResidualArtifact:
    pairs = tuple((0.0, 0.0) for _ in range(50))
    base = dict(
        schema_version=1,
        model_id="test",
        prop_type="PTS",
        source_model_version="v2",
        n_rows=50,
        calibration_lambda=1.0,
        pairs=pairs,
        sha256="abc",
    )
    base.update(overrides)
    return ResidualArtifact(**base)


class CalibrationTests(unittest.TestCase):
    def test_artifact_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pts.json"
            saved = save_residual_artifact(
                path,
                model_id="m",
                prop_type="PTS",
                source_model_version="v2",
                calibration_lambda=0.7,
                pairs=[(0.1, 0.02), (-0.2, -0.03)],
            )
            loaded = load_residual_artifact(path)
        self.assertEqual(loaded.n_rows, 2)
        self.assertAlmostEqual(loaded.calibration_lambda, 0.7)
        self.assertNotEqual(saved.sha256, "")

    def test_calibrate_shrinks_toward_half(self) -> None:
        self.assertAlmostEqual(calibrate_probability(0.8, 1.0), 0.8)
        self.assertAlmostEqual(calibrate_probability(0.8, 0.0), 0.5)
        self.assertAlmostEqual(calibrate_probability(0.8, 0.5), 0.65)

    def test_fit_lambda_prefers_best_brier(self) -> None:
        pairs = [(0.9, 1), (0.9, 1), (0.1, 0), (0.1, 0)]
        self.assertAlmostEqual(fit_calibration_lambda(pairs), 1.0)


class SimulationTests(unittest.TestCase):
    def test_projection_uses_residual_pairs(self) -> None:
        features = _features()
        minutes = project_minutes(features)
        artifact = _artifact(pairs=((1.0, 0.05), (-1.0, -0.05)) * 25)
        result = simulate_prop(
            features=features,
            minutes=minutes,
            projected_rate=0.70,
            line=20.5,
            residuals=artifact,
            simulations=2000,
            seed_material="test",
        )
        self.assertAlmostEqual(
            result.over_probability + result.under_probability + result.push_probability,
            1.0,
            places=6,
        )
        self.assertGreater(result.projected_mean, 0.0)

    def test_deterministic_seed(self) -> None:
        features = _features()
        minutes = project_minutes(features)
        artifact = _artifact()
        first = simulate_prop(
            features=features,
            minutes=minutes,
            projected_rate=0.70,
            line=20.5,
            residuals=artifact,
            simulations=500,
        )
        second = simulate_prop(
            features=features,
            minutes=minutes,
            projected_rate=0.70,
            line=20.5,
            residuals=artifact,
            simulations=500,
        )
        self.assertEqual(first.over_probability, second.over_probability)

    def test_rate_blend_prefers_recent(self) -> None:
        projection = project_rate(_features(rate_l5=0.9, rate_recency_weighted=0.9, rate_season=0.5))
        self.assertGreater(projection.projected_rate, 0.7)


class ValueTests(unittest.TestCase):
    def test_value_labels(self) -> None:
        self.assertEqual(value_label(None), "unpriced")
        self.assertEqual(value_label(0.05), "playable")
        self.assertEqual(value_label(0.0), "thin")
        self.assertEqual(value_label(-0.10), "no_value")

    def test_binary_and_push_ev(self) -> None:
        self.assertAlmostEqual(expected_value(0.5, -110), 0.5 * (100 / 110) - 0.5)
        ev = expected_value_with_push(0.6, 0.3, -110)
        self.assertAlmostEqual(ev, 0.6 * (100 / 110) - 0.3)


class PriceIndependenceTests(unittest.TestCase):
    def _line(self, over: int, under: int) -> PropLine:
        return PropLine(
            event_id="evt",
            game_date=date(2026, 6, 10),
            player_name_raw="Test Player",
            player_name_norm="test player",
            team="NY",
            opponent="PHX",
            prop_type="PTS",
            line=20.5,
            bookmaker="fanduel",
            source="fixture",
            collected_at=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc),
            over_odds=over,
            under_odds=under,
        )

    def test_prop_pick_does_not_depend_on_price(self) -> None:
        from wnba_props.modeling.props_forecast import forecast_prop

        features = _features()
        artifact = _artifact(pairs=((0.5, 0.02), (-0.5, -0.02)) * 25)
        first = forecast_prop(features=features, line=self._line(-110, -110), residuals=artifact)
        second = forecast_prop(features=features, line=self._line(-400, 300), residuals=artifact)
        assert first is not None and second is not None
        self.assertEqual(first.pick_side, second.pick_side)
        self.assertAlmostEqual(first.pick_probability, second.pick_probability)
        self.assertNotEqual(first.ev, second.ev)


class MarketAwareDecisionTests(unittest.TestCase):
    def _line(self, over: int, under: int) -> PropLine:
        return PropLine(
            event_id="evt",
            game_date=date(2026, 6, 10),
            player_name_raw="Test Player",
            player_name_norm="test player",
            team="NY",
            opponent="PHX",
            prop_type="PTS",
            line=20.5,
            bookmaker="fanduel",
            source="fixture",
            collected_at=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc),
            over_odds=over,
            under_odds=under,
        )

    def test_pick_side_respects_ev_when_enabled(self) -> None:
        from wnba_props.modeling.props_forecast import _pick_side

        side, probability = _pick_side(0.60, 0.40, 0.0, -0.20, 0.30, True)
        self.assertEqual(side, "UNDER")
        self.assertAlmostEqual(probability, 0.40)

    def test_market_blend_moves_probability_toward_no_vig(self) -> None:
        from wnba_props.modeling.props_forecast import forecast_prop

        artifact = _artifact(pairs=((0.0, 0.0),) * 50)
        plain = forecast_prop(features=_features(), line=self._line(-110, -110), residuals=artifact)
        blended = forecast_prop(
            features=_features(),
            line=self._line(-110, -110),
            residuals=artifact,
            market_weight=0.5,
        )
        assert plain is not None and blended is not None
        self.assertAlmostEqual(blended.market_over_probability or 0.0, 0.5)
        self.assertAlmostEqual(
            blended.over_probability,
            0.5 * plain.over_probability + 0.5 * 0.5,
        )


class EnvironmentFactorTests(unittest.TestCase):
    def test_missing_total_is_neutral(self) -> None:
        self.assertEqual(game_environment_factor(None), 1.0)

    def test_high_total_lifts_rate(self) -> None:
        self.assertGreater(game_environment_factor(180.0, 164.0), 1.0)

    def test_low_total_cuts_rate(self) -> None:
        self.assertLess(game_environment_factor(150.0, 164.0), 1.0)

    def test_adjustment_is_capped(self) -> None:
        self.assertLessEqual(game_environment_factor(400.0, 164.0), 1.05)
        self.assertGreaterEqual(game_environment_factor(1.0, 164.0), 0.95)

    def test_project_rate_uses_environment(self) -> None:
        low = project_rate(_features(), game_total=156.0)
        high = project_rate(_features(), game_total=172.0)
        self.assertLess(low.projected_rate, high.projected_rate)

    def test_project_rate_blends_positional_allowance(self) -> None:
        features = _features(opponent_allowance=80.0, opponent_positional_allowance=90.0)
        team_only = project_rate(features, league_baseline=80.0)
        blended = project_rate(
            features, league_baseline=80.0, positional_baseline=80.0
        )
        self.assertGreater(blended.projected_rate, team_only.projected_rate)


class DnpProbabilityTests(unittest.TestCase):
    def test_excluded_status_is_certain(self) -> None:
        self.assertEqual(dnp_probability(_features(), player_status="out"), 1.0)

    def test_questionable_exceeds_healthy_base(self) -> None:
        base = dnp_probability(_features())
        self.assertGreater(
            dnp_probability(_features(), player_status="questionable"), base
        )

    def test_bench_higher_than_starter(self) -> None:
        starter = dnp_probability(_features(minutes_avg_l5=32.0, starting=True))
        bench = dnp_probability(_features(minutes_avg_l5=10.0, starting=False))
        self.assertGreater(bench, starter)

    def test_simulation_reports_voids_and_keeps_distribution(self) -> None:
        features = _features()
        minutes = project_minutes(features, player_status="questionable")
        artifact = _artifact()
        result = simulate_prop(
            features=features,
            minutes=minutes,
            projected_rate=0.70,
            line=20.5,
            residuals=artifact,
            simulations=4000,
            seed_material="dnp",
        )
        self.assertGreater(result.void_probability, 0.0)
        self.assertAlmostEqual(
            result.over_probability + result.under_probability + result.push_probability,
            1.0,
            places=6,
        )


class PositionTests(unittest.TestCase):
    def test_position_classes(self) -> None:
        self.assertEqual(position_class("G"), "G")
        self.assertEqual(position_class("G-F"), "G")
        self.assertEqual(position_class("F-C"), "F")
        self.assertEqual(position_class("Center"), "C")
        self.assertEqual(position_class(""), "")


if __name__ == "__main__":
    unittest.main()
