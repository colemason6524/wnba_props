from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from grade_projection_shadow import _pending_snapshot_paths, _resolve_snapshot_paths
from wnba_props.shadow.grading import grade_shadow_projections
from wnba_props.shadow.sources import (
    ShadowBoxscoreStatLine,
    ShadowEspnBoxscoreSource,
    ShadowEspnSlateSource,
    ShadowGameStatus,
)


def _projection(
    *,
    player: str,
    game_id: str = "final-game",
    line: float = 17.5,
    projected_mean: float = 18.0,
    projected_minutes: float = 32.0,
    over_probability: float = 0.60,
    model_side: str = "OVER",
    over_odds: int | None = -110,
    under_odds: int | None = -110,
) -> dict:
    return {
        "model_version": "fixture-v1",
        "screen_date": "2026-08-04",
        "game_id": game_id,
        "player_name": player,
        "player_name_norm": player.lower(),
        "team": "NY",
        "opponent": "PHX",
        "prop_type": "PTS",
        "line": line,
        "bookmaker": "fanduel",
        "line_collected_at": "2026-08-04T15:00:00+00:00",
        "over_odds": over_odds,
        "under_odds": under_odds,
        "projected_mean": projected_mean,
        "projected_minutes": projected_minutes,
        "over_probability": over_probability,
        "under_probability": 1.0 - over_probability,
        "push_probability": 0.0,
        "model_side": model_side,
        "decision": "RESEARCH_ONLY",
    }


def _stat_line(player: str, *, minutes: float, points: int) -> ShadowBoxscoreStatLine:
    return ShadowBoxscoreStatLine(
        player_name_raw=player,
        player_name_norm=player.lower(),
        team="NY",
        minutes=minutes,
        points=points,
        rebounds=5,
        assists=4,
        threes_made=2,
    )


class ShadowGradingTests(unittest.TestCase):
    def test_status_parser_distinguishes_final_from_in_progress(self) -> None:
        statuses = ShadowEspnSlateSource.parse_game_statuses(
            {
                "events": [
                    {
                        "id": "final-game",
                        "status": {
                            "type": {"state": "post", "completed": True, "detail": "Final"}
                        },
                    },
                    {
                        "id": "live-game",
                        "status": {
                            "type": {"state": "in", "completed": False, "detail": "3rd Quarter"}
                        },
                    },
                ]
            }
        )

        self.assertTrue(statuses["final-game"].completed)
        self.assertEqual("post", statuses["final-game"].state)
        self.assertFalse(statuses["live-game"].completed)
        self.assertEqual("3rd Quarter", statuses["live-game"].detail)

    def test_shadow_boxscore_parser_reads_minutes_and_points(self) -> None:
        parsed = ShadowEspnBoxscoreSource.parse_boxscore(
            {
                "boxscore": {
                    "players": [
                        {
                            "team": {"displayName": "New York Liberty"},
                            "statistics": [
                                {
                                    "labels": ["MIN", "FG", "3PT", "REB", "AST", "PTS"],
                                    "athletes": [
                                        {
                                            "athlete": {"displayName": "Test Player"},
                                            "stats": ["34:30", "7-14", "2-5", "5", "4", "20"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        )

        row = parsed[("test player", "NY")]
        self.assertEqual(34.5, row.minutes)
        self.assertEqual(20, row.points)
        self.assertEqual(2, row.threes_made)

    def test_final_pending_dnp_and_missing_player_are_kept_distinct(self) -> None:
        projections = [
            _projection(player="Winner"),
            _projection(player="Pending", game_id="live-game"),
            _projection(player="Dnp Player"),
            _projection(player="Name Mismatch"),
        ]
        statuses = {
            "final-game": ShadowGameStatus("final-game", "post", True, "Final"),
            "live-game": ShadowGameStatus("live-game", "in", False, "3rd Quarter"),
        }
        boxscores = {
            "final-game": {
                ("winner", "NY"): _stat_line("Winner", minutes=34.5, points=20),
                ("dnp player", "NY"): _stat_line("Dnp Player", minutes=0.0, points=0),
            }
        }

        report = grade_shadow_projections(
            projections,
            game_statuses=statuses,
            boxscores=boxscores,
        )

        self.assertEqual(1, report["summary"]["graded_count"])
        self.assertEqual(1, report["summary"]["pending_count"])
        self.assertEqual(1, report["summary"]["void_dnp_count"])
        self.assertEqual(1, report["summary"]["unresolved_count"])
        self.assertEqual("final_boxscore_player_missing", report["unresolved"][0]["reason"])

        grade = report["graded"][0]
        self.assertEqual("win", grade["selection_outcome"])
        self.assertAlmostEqual(0.9091, grade["profit_units"], places=4)
        self.assertEqual(2.0, grade["projection_error_points"])
        self.assertEqual(2.5, grade["projection_error_minutes"])
        self.assertAlmostEqual(0.16, grade["over_brier_score"], places=6)
        self.assertEqual("0.6-0.7", report["over_probability_calibration"][0]["bucket"])

    def test_push_is_not_used_for_binary_probability_calibration(self) -> None:
        projection = _projection(player="Push Player", line=20.0)
        report = grade_shadow_projections(
            [projection],
            game_statuses={
                "final-game": ShadowGameStatus("final-game", "post", True, "Final")
            },
            boxscores={
                "final-game": {
                    ("push player", "NY"): _stat_line("Push Player", minutes=30.0, points=20)
                }
            },
        )

        self.assertEqual("push", report["graded"][0]["selection_outcome"])
        self.assertEqual(0.0, report["graded"][0]["profit_units"])
        self.assertIsNone(report["graded"][0]["over_brier_score"])
        self.assertIsNone(report["summary"]["over_brier_score"])
        self.assertEqual([], report["over_probability_calibration"])

    def test_unpriced_selection_is_graded_but_excluded_from_units(self) -> None:
        projection = _projection(player="No Price", over_odds=None, under_odds=None)
        report = grade_shadow_projections(
            [projection],
            game_statuses={
                "final-game": ShadowGameStatus("final-game", "post", True, "Final")
            },
            boxscores={
                "final-game": {
                    ("no price", "NY"): _stat_line("No Price", minutes=30.0, points=20)
                }
            },
        )

        self.assertEqual(1, report["summary"]["selected_count"])
        self.assertEqual(0, report["summary"]["priced_selected_count"])
        self.assertIsNone(report["summary"]["flat_stake_units"])
        self.assertIsNone(report["summary"]["flat_stake_roi"])

    def test_all_pending_skips_snapshot_with_latest_terminal_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed_snapshot = root / "shadow_projection_completed.json"
            pending_snapshot = root / "shadow_projection_pending.json"
            completed_snapshot.write_text("{}")
            pending_snapshot.write_text("{}")
            pending_report = root / "shadow_grade_old.json"
            pending_report.write_text(
                json.dumps(
                    {
                        "mode": "shadow_projection_grade",
                        "generated_at": "2026-08-05T01:00:00+00:00",
                        "source_snapshot": str(completed_snapshot.resolve()),
                        "boxscore_fetch_errors": [],
                        "summary": {"pending_count": 1},
                        "unresolved": [],
                    }
                )
            )
            final_report = root / "shadow_grade_final.json"
            final_report.write_text(
                json.dumps(
                    {
                        "mode": "shadow_projection_grade",
                        "generated_at": "2026-08-05T02:00:00+00:00",
                        "source_snapshot": str(completed_snapshot.resolve()),
                        "boxscore_fetch_errors": [],
                        "summary": {"pending_count": 0},
                        "unresolved": [],
                    }
                )
            )

            result = _pending_snapshot_paths(
                [completed_snapshot, pending_snapshot],
                [pending_report, final_report],
            )

            self.assertEqual([pending_snapshot], result)

    def test_all_pending_is_clean_when_no_snapshots_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("grade_projection_shadow.OUTPUTS_DIR", Path(directory)):
                self.assertEqual([], _resolve_snapshot_paths(None, all_pending=True))


if __name__ == "__main__":
    unittest.main()
