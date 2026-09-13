from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from wnba_props.features.team import TeamFeatures
from wnba_props.modeling.game import (
    fit_logistic,
    fit_ridge,
    margin_vector,
    total_vector,
    winner_vector,
)
from wnba_props.modeling.game_forecast import (
    GameEngine,
    GameMarket,
    forecast_game,
    load_game_engine,
    save_game_engine,
)


def _team(
    *,
    team: str,
    opponent: str,
    is_home: bool,
    ppg: float,
    opp_ppg: float,
    margin: float,
) -> TeamFeatures:
    return TeamFeatures(
        team=team,
        opponent=opponent,
        game_date=date(2026, 6, 1),
        is_home=is_home,
        games_played=10,
        ppg_last_5=ppg,
        ppg_last_10=ppg,
        ppg_season=ppg,
        opp_ppg_allowed_last_5=opp_ppg,
        opp_ppg_allowed_last_10=opp_ppg,
        opp_ppg_allowed_season=opp_ppg,
        margin_last_5=margin,
        rest_days=2,
        pace_proxy_last_5=160.0,
        pace_proxy_season=160.0,
    )


class RegressionTests(unittest.TestCase):
    def test_ridge_learns_linear_relationship(self) -> None:
        rows = [[float(x)] for x in range(20)]
        targets = [3.0 * x + 1.0 for x in range(20)]
        model = fit_ridge(rows, targets, l2=0.0)
        self.assertAlmostEqual(model.predict([10.0]), 31.0, delta=1.0)

    def test_logistic_separates_classes(self) -> None:
        rows = [[float(x)] for x in range(20)]
        targets = [0 if x < 10 else 1 for x in range(20)]
        model = fit_logistic(rows, targets, l2=0.0, iterations=1000)
        self.assertLess(model.predict_proba([0.0]), 0.5)
        self.assertGreater(model.predict_proba([19.0]), 0.5)


class GameForecastTests(unittest.TestCase):
    def test_forecast_produces_probabilities_and_value(self) -> None:
        engine = GameEngine(
            winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=500),
            margin=fit_ridge([[1.0], [-1.0]], [5.0, -5.0], l2=0.0),
            total=fit_ridge([[1.0], [-1.0]], [170.0, 150.0], l2=0.0),
        )
        home = _team(team="NY", opponent="PHX", is_home=True, ppg=85, opp_ppg=78, margin=6)
        away = _team(team="PHX", opponent="NY", is_home=False, ppg=78, opp_ppg=85, margin=-6)
        market = GameMarket(
            home_moneyline=-150,
            away_moneyline=130,
            home_spread=-3.5,
            home_spread_price=-110,
            away_spread_price=-110,
            total_line=164.5,
            over_price=-110,
            under_price=-110,
        )
        forecast = forecast_game(home=home, away=away, engine=engine, market=market)
        self.assertAlmostEqual(forecast.p_home + forecast.p_away, 1.0)
        self.assertIn(forecast.winner_pick, {"HOME", "AWAY"})
        self.assertIn(forecast.spread_pick, {"HOME", "AWAY"})
        self.assertIn(forecast.total_pick, {"OVER", "UNDER"})
        self.assertIsNotNone(forecast.winner_value)

    def test_pick_is_price_independent(self) -> None:
        engine = GameEngine(
            winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=500),
            margin=fit_ridge([[1.0], [-1.0]], [5.0, -5.0], l2=0.0),
            total=fit_ridge([[1.0], [-1.0]], [170.0, 150.0], l2=0.0),
        )
        home = _team(team="NY", opponent="PHX", is_home=True, ppg=85, opp_ppg=78, margin=6)
        away = _team(team="PHX", opponent="NY", is_home=False, ppg=78, opp_ppg=85, margin=-6)
        cheap = GameMarket(home_moneyline=-110, away_moneyline=-110, home_spread=-3.5,
                           home_spread_price=-110, away_spread_price=-110,
                           total_line=164.5, over_price=-110, under_price=-110)
        rich = GameMarket(home_moneyline=-500, away_moneyline=400, home_spread=-3.5,
                          home_spread_price=-200, away_spread_price=170,
                          total_line=164.5, over_price=-200, under_price=170)
        first = forecast_game(home=home, away=away, engine=engine, market=cheap)
        second = forecast_game(home=home, away=away, engine=engine, market=rich)
        self.assertEqual(first.winner_pick, second.winner_pick)
        self.assertEqual(first.spread_pick, second.spread_pick)
        self.assertEqual(first.total_pick, second.total_pick)
        self.assertAlmostEqual(first.winner_probability, second.winner_probability)
        self.assertAlmostEqual(first.total_probability, second.total_probability)

    def test_engine_roundtrip(self) -> None:
        engine = GameEngine(
            winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=200),
            margin=fit_ridge([[1.0], [-1.0]], [5.0, -5.0], l2=0.0),
            total=fit_ridge([[1.0], [-1.0]], [170.0, 150.0], l2=0.0),
            trained_games=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.json"
            save_game_engine(path, engine)
            loaded = load_game_engine(path)
        self.assertEqual(loaded.version, engine.version)
        self.assertAlmostEqual(
            loaded.winner.predict_proba([1.0]), engine.winner.predict_proba([1.0])
        )


if __name__ == "__main__":
    unittest.main()
