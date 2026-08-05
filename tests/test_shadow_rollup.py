from __future__ import annotations

import unittest

from wnba_props.shadow.rollup import build_shadow_rollup


def _row(
    *,
    player: str,
    lead: float,
    game_id: str = "game-1",
    screen_date: str = "2026-08-05",
) -> dict:
    return {
        "projection_id": f"{game_id}-{player}-{lead}",
        "model_version": "model-v1",
        "screen_date": screen_date,
        "game_id": game_id,
        "player_name_norm": player,
        "prop_type": "PTS",
        "bookmaker": "fanduel",
        "capture_lead_minutes": lead,
        "line": 17.5,
        "over_odds": -110,
        "under_odds": -110,
        "projected_mean": 18.0,
        "projected_minutes": 30.0,
        "percentile_10": 10.0,
        "percentile_90": 25.0,
        "team_spread": -2.5,
        "game_total": 164.5,
        "actual_points": 20,
        "actual_minutes": 32.0,
        "actual_over": True,
        "projection_error_points": 2.0,
        "absolute_error_points": 2.0,
        "projection_error_minutes": 2.0,
        "absolute_error_minutes": 2.0,
        "over_probability": 0.60,
        "over_brier_score": 0.16,
        "model_side": "OVER",
        "selection_outcome": "win",
        "profit_units": 0.9091,
    }


class ShadowRollupTests(unittest.TestCase):
    def test_rollup_uses_one_closest_valid_pregame_capture(self) -> None:
        reports = [
            {
                "graded": [
                    _row(player="player-a", lead=60.0),
                    _row(player="player-a", lead=30.0),
                    _row(player="late-player", lead=-5.0),
                    _row(player="early-player", lead=120.0),
                ]
            }
        ]

        rollup = build_shadow_rollup(reports)
        primary = rollup["primary_pregame"]

        self.assertEqual(4, rollup["all_resolved_diagnostics"]["projection_count"])
        self.assertEqual(4, rollup["resolution_totals"]["graded_count"])
        self.assertEqual(1, primary["projection_count"])
        self.assertEqual(1, rollup["duplicate_primary_candidates_removed"])
        self.assertEqual(2, rollup["excluded_from_primary"]["count"])
        self.assertEqual(2.0, primary["mean_absolute_error_points"])
        self.assertEqual(2.5, primary["sportsbook_line_mean_absolute_error"])
        self.assertEqual(-0.5, primary["model_mae_minus_line_mae"])
        self.assertEqual(0.16, primary["model_over_brier_score"])
        self.assertEqual(0.25, primary["market_no_vig_over_brier_score"])
        self.assertEqual("COLLECTING", rollup["evidence_gate"]["status"])

    def test_evidence_gate_requires_multiple_slates_games_and_prices(self) -> None:
        rows = []
        for index in range(100):
            rows.append(
                _row(
                    player=f"player-{index}",
                    lead=30.0,
                    game_id=f"game-{index % 20}",
                    screen_date=f"2026-08-{5 + (index % 7):02d}",
                )
            )

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual(100, rollup["primary_pregame"]["projection_count"])
        self.assertEqual(20, rollup["primary_pregame"]["game_count"])
        self.assertEqual(7, rollup["primary_pregame"]["slate_count"])
        self.assertEqual("READY_FOR_REVIEW", rollup["evidence_gate"]["status"])


if __name__ == "__main__":
    unittest.main()
