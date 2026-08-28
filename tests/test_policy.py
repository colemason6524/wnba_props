from __future__ import annotations

import unittest
from datetime import date

from backtest import ResolvedPrediction, _profit_units
from run_nightly import _line_source_failure_message
from wnba_props.config import Settings
from wnba_props.models import Candidate
from wnba_props.output import render_discord_embeds
from wnba_props.screener import (
    _injury_score_adjustment,
    _is_playoff_window,
    _matchup_adjustment,
    _playoff_context_flags,
    _playoff_score_adjustment,
    _score_candidate,
)


def _candidate(player_name: str, flags: list[str]) -> Candidate:
    return Candidate(
        player_name=player_name,
        team="NY",
        opponent="PHX",
        prop_type="PTS",
        side="OVER",
        line=19.5,
        bookmaker="fanduel",
        hits_last_5=4,
        played_last_5=5,
        hits_last_10=8,
        played_last_10=10,
        avg_last_5=23.0,
        avg_last_10=21.5,
        median_last_5=22.0,
        median_last_10=21.0,
        season_avg=20.0,
        avg_minutes_last_5=32.0,
        avg_minutes_last_10=31.0,
        delta_avg_last_5=3.5,
        score=9,
        american_odds=-110,
        decimal_odds=1.91,
        flags=flags,
    )


class PolicyTests(unittest.TestCase):
    def test_season_disagreement_receives_real_penalty(self) -> None:
        settings = Settings()
        positive_score, positive_flags = _score_candidate(settings, 4, 5, 7, 2.0, 1.0, 30.0)
        negative_score, negative_flags = _score_candidate(settings, 4, 5, 7, 2.0, -1.0, 30.0)

        self.assertNotIn("SEASON-", positive_flags)
        self.assertIn("SEASON-", negative_flags)
        self.assertEqual(3, positive_score - negative_score)

    def test_team_out_is_a_penalty_for_both_sides(self) -> None:
        self.assertEqual(-1, _injury_score_adjustment("OVER", ["TEAM_OUT"]))
        self.assertEqual(-1, _injury_score_adjustment("UNDER", ["TEAM_OUT"]))

    def test_discord_suppresses_risk_flags_but_keeps_clean_candidate(self) -> None:
        embeds = render_discord_embeds(
            [_candidate("Clean Player", []), _candidate("Season Risk", ["SEASON-"])],
            screen_date=date(2026, 8, 2),
            games_count=1,
            prop_line_count=2,
            qualified_count=2,
            displayed_count=2,
            line_source="playerprops",
            bookmaker="FANDUEL",
            min_score=8,
        )

        rendered = str(embeds)
        self.assertIn("Clean Player", rendered)
        self.assertNotIn("Season Risk", rendered)
        self.assertIn("1 risk-flagged plays suppressed", embeds[0]["description"])

    def test_discord_defaults_to_five_plays_per_side(self) -> None:
        candidates = [_candidate(f"Player {index}", []) for index in range(6)]

        embeds = render_discord_embeds(
            candidates,
            screen_date=date(2026, 8, 2),
            games_count=1,
            prop_line_count=6,
            qualified_count=6,
            displayed_count=6,
            line_source="playerprops",
            bookmaker="FANDUEL",
            min_score=8,
        )

        play_fields = [field for field in embeds[0]["fields"] if field["name"].startswith("Over |")]
        self.assertEqual(5, len(play_fields))

    def test_line_source_failure_message_is_distinct_from_no_plays(self) -> None:
        settings = Settings(screen_date=date(2026, 8, 1), line_source="playerprops")
        source = type(
            "Source",
            (),
            {
                "diagnostics": {
                    "payload_events": 2,
                    "matched_events": 0,
                    "selected_book_plays": 0,
                },
                "failures": ["PlayerProps event NYL/PHX did not match the ESPN slate."],
            },
        )()

        message = _line_source_failure_message(settings, [object(), object()], source)

        self.assertIn("WNBA props data failure", message)
        self.assertIn("PlayerProps events: 2", message)
        self.assertIn("not a normal no-plays result", message)

    def test_playoff_window_follows_wnba_calendar(self) -> None:
        self.assertTrue(_is_playoff_window(date(2026, 9, 30)))
        self.assertTrue(_is_playoff_window(date(2026, 10, 15)))
        self.assertFalse(_is_playoff_window(date(2026, 8, 28)))
        self.assertFalse(_is_playoff_window(date(2026, 6, 5)))

    def test_playoff_role_flag_only_fires_in_wnba_playoff_window(self) -> None:
        self.assertEqual(
            ["PLAYOFF_ROLE"],
            _playoff_context_flags(date(2026, 9, 30), "OVER", 28.0, ["HOT_MOD"]),
        )
        self.assertEqual([], _playoff_context_flags(date(2026, 6, 5), "OVER", 28.0, ["HOT_MOD"]))
        self.assertEqual([], _playoff_context_flags(date(2026, 9, 30), "UNDER", 28.0, ["HOT_MOD"]))
        self.assertEqual([], _playoff_context_flags(date(2026, 9, 30), "OVER", 33.0, ["HOT_MOD"]))

    def test_playoff_score_adjustment_penalizes_role_driven_overs(self) -> None:
        self.assertEqual(-1, _playoff_score_adjustment(["PLAYOFF_ROLE"]))
        self.assertEqual(-2, _playoff_score_adjustment(["PLAYOFF_ROLE", "ROLE_UP"]))
        self.assertEqual(0, _playoff_score_adjustment(["HOT_MOD"]))

    def test_matchup_adjustment_weights_recent_form_more_in_playoffs(self) -> None:
        matchup = {
            "season_avg": 9.0,
            "recent_avg": 13.0,
            "league_avg": 10.0,
            "season_sample": 20,
            "recent_sample": 10,
        }

        playoff_signal, playoff_flags = _matchup_adjustment(date(2026, 9, 30), "PTS", "OVER", matchup)
        regular_signal, regular_flags = _matchup_adjustment(date(2026, 6, 5), "PTS", "OVER", matchup)

        self.assertEqual(1, playoff_signal)
        self.assertIn("MATCHUP_PLUS", playoff_flags)
        self.assertEqual(0, regular_signal)
        self.assertEqual([], regular_flags)

    def test_profit_units_require_a_stored_price(self) -> None:
        base = {
            "screen_date": date(2026, 8, 1),
            "player_name": "Priced Player",
            "team": "NY",
            "opponent": "PHX",
            "prop_type": "PTS",
            "side": "OVER",
            "line": 19.5,
            "score": 9,
            "flags": [],
            "actual": 22.0,
            "outcome": "win",
            "edge": 2.5,
            "resolution_method": "exact",
        }
        unpriced = ResolvedPrediction(**base)
        priced = ResolvedPrediction(**base, american_odds=-110, decimal_odds=1.91)

        self.assertIsNone(_profit_units(unpriced))
        self.assertAlmostEqual(0.91, _profit_units(priced) or 0.0)


if __name__ == "__main__":
    unittest.main()
