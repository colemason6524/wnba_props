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
        "model_config_hash": "hash-a",
        "code_commit": "abc123",
        "code_dirty": False,
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

    def test_evidence_gate_marks_mixed_models(self) -> None:
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
        for index, row in enumerate(rows):
            row["model_config_hash"] = "hash-a" if index % 2 == 0 else "hash-b"

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual("MIXED_MODELS", rollup["evidence_gate"]["status"])
        self.assertEqual(2, len(rollup["model_breakdown"]))

    def test_multiple_code_commits_are_mixed_models(self) -> None:
        rows = [
            _row(player=f"player-{index}", lead=30.0, game_id=f"game-{index % 20}")
            for index in range(100)
        ]
        for index, row in enumerate(rows):
            row["code_commit"] = "commit-a" if index % 2 == 0 else "commit-b"

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual("MIXED_MODELS", rollup["evidence_gate"]["status"])
        self.assertEqual(2, len(rollup["model_breakdown"]))

    def test_dirty_rows_stay_diagnostic_not_primary(self) -> None:
        clean_rows = [
            _row(
                player=f"clean-{index}",
                lead=30.0,
                game_id=f"clean-game-{index % 7}",
                screen_date=f"2026-08-{5 + (index % 7):02d}",
            )
            for index in range(14)
        ]
        dirty_rows = [
            _row(player=f"dirty-{index}", lead=30.0, game_id="dirty-game")
            for index in range(3)
        ]
        for row in dirty_rows:
            row["code_dirty"] = True

        rollup = build_shadow_rollup([{"graded": clean_rows + dirty_rows}])

        self.assertEqual(17, rollup["all_resolved_diagnostics"]["projection_count"])
        self.assertEqual(14, rollup["primary_pregame"]["projection_count"])
        self.assertEqual(3, rollup["excluded_from_primary"]["reasons"].get("code_dirty", 0))
        self.assertEqual("COLLECTING", rollup["evidence_gate"]["status"])

    def test_missing_code_state_is_excluded_from_primary(self) -> None:
        rows = [
            _row(
                player=f"player-{index}",
                lead=30.0,
                game_id=f"game-{index % 20}",
                screen_date=f"2026-08-{5 + (index % 7):02d}",
            )
            for index in range(100)
        ]
        for row in rows:
            row.pop("code_commit", None)
            row.pop("code_dirty", None)

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual(0, rollup["primary_pregame"]["projection_count"])
        self.assertEqual(
            100, rollup["excluded_from_primary"]["reasons"].get("code_commit_missing", 0)
        )
        self.assertEqual("COLLECTING", rollup["evidence_gate"]["status"])

    def test_missing_commit_is_excluded_as_code_commit_missing(self) -> None:
        rows = [
            _row(
                player=f"player-{index}",
                lead=30.0,
                game_id=f"game-{index % 20}",
                screen_date=f"2026-08-{5 + (index % 7):02d}",
            )
            for index in range(100)
        ]
        for row in rows:
            row.pop("code_commit", None)

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual(0, rollup["primary_pregame"]["projection_count"])
        self.assertEqual(
            100, rollup["excluded_from_primary"]["reasons"].get("code_commit_missing", 0)
        )
        self.assertEqual("COLLECTING", rollup["evidence_gate"]["status"])

    def test_commit_without_dirty_flag_is_code_state_missing(self) -> None:
        rows = [
            _row(
                player=f"player-{index}",
                lead=30.0,
                game_id=f"game-{index % 20}",
                screen_date=f"2026-08-{5 + (index % 7):02d}",
            )
            for index in range(100)
        ]
        for row in rows:
            row.pop("code_dirty", None)

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual(0, rollup["primary_pregame"]["projection_count"])
        self.assertEqual(
            100, rollup["excluded_from_primary"]["reasons"].get("code_state_missing", 0)
        )
        self.assertEqual("COLLECTING", rollup["evidence_gate"]["status"])

    def test_dirty_rows_cannot_satisfy_gate(self) -> None:
        rows = [
            _row(
                player=f"player-{index}",
                lead=30.0,
                game_id=f"game-{index % 20}",
                screen_date=f"2026-08-{5 + (index % 7):02d}",
            )
            for index in range(100)
        ]
        for row in rows:
            row["code_dirty"] = True

        rollup = build_shadow_rollup([{"graded": rows}])

        self.assertEqual(0, rollup["primary_pregame"]["projection_count"])
        self.assertEqual("COLLECTING", rollup["evidence_gate"]["status"])


if __name__ == "__main__":
    unittest.main()
