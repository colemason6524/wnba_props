from __future__ import annotations

import unittest
from datetime import date

from backtest import ResolvedPrediction, _profit_units
from run_nightly import _line_source_failure_message
from wnba_props.config import Settings
from wnba_props.models import Candidate
from wnba_props.output import render_discord_embeds
from wnba_props.screener import _injury_score_adjustment, _score_candidate


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
