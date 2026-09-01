from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from wnba_props.config import OUTPUTS_DIR, Settings
from wnba_props.models import Candidate, Game, PlayerGameLog, PropLine, TeamInjury
from wnba_props.screener import filter_logs_as_of, screen_candidates
from run_nightly import (
    apply_pregame_guard,
    classify_run_health,
    export_run_history,
    run_provenance,
    write_delivery_status,
)


def _log(
    player: str,
    game_date: date,
    points: int,
    minutes: float = 30.0,
    did_play: bool = True,
    team: str = "NY",
    opponent: str = "PHX",
) -> PlayerGameLog:
    return PlayerGameLog(
        player_name_raw=player,
        player_name_norm=player.lower(),
        game_date=game_date,
        team=team,
        opponent=opponent,
        minutes=minutes,
        points=points,
        rebounds=0,
        assists=0,
        threes_made=0,
        did_play=did_play,
        source="test",
    )


def _line(
    player: str,
    team: str = "NY",
    opponent: str = "PHX",
    prop_type: str = "PTS",
    line: float = 19.5,
) -> PropLine:
    return PropLine(
        event_id="1",
        game_date=date(2026, 9, 17),
        player_name_raw=player,
        player_name_norm=player.lower(),
        team=team,
        opponent=opponent,
        prop_type=prop_type,
        line=line,
        bookmaker="fanduel",
        source="test",
        collected_at=datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc),
    )


SCREEN_DATE = date(2026, 9, 17)


def _settings(**overrides) -> Settings:
    return Settings(screen_date=SCREEN_DATE, **overrides)


class PointInTimeFilterTests(unittest.TestCase):
    def test_same_day_and_future_logs_are_excluded(self) -> None:
        logs = {
            "player": [
                _log("Player", date(2026, 9, 17), 50),
                _log("Player", date(2026, 9, 20), 50),
                _log("Player", date(2026, 9, 16), 10),
                _log("Player", date(2026, 9, 15), 10),
            ]
        }
        filtered = filter_logs_as_of(logs, SCREEN_DATE)
        self.assertEqual([date(2026, 9, 16), date(2026, 9, 15)], [log.game_date for log in filtered["player"]])

    def test_dnp_and_zero_minute_rows_are_excluded(self) -> None:
        logs = {
            "player": [
                _log("Player", date(2026, 9, 16), 0, minutes=0.0, did_play=False),
                _log("Player", date(2026, 9, 15), 0, minutes=0.0, did_play=True),
                _log("Player", date(2026, 9, 14), 12, minutes=25.0),
            ]
        }
        filtered = filter_logs_as_of(logs, SCREEN_DATE)
        self.assertEqual([date(2026, 9, 14)], [log.game_date for log in filtered["player"]])

    def test_player_without_usable_logs_is_dropped(self) -> None:
        logs = {"ghost": [_log("Ghost", date(2026, 9, 16), 0, minutes=0.0, did_play=False)]}
        filtered = filter_logs_as_of(logs, SCREEN_DATE)
        self.assertNotIn("ghost", filtered)


class UnavailablePlayerTests(unittest.TestCase):
    def _screen(self, injuries: list[TeamInjury]):
        logs = {
            "player": [
                _log("Player", date(2026, 9, 16), 25),
                _log("Player", date(2026, 9, 15), 25),
                _log("Player", date(2026, 9, 14), 25),
                _log("Player", date(2026, 9, 13), 25),
                _log("Player", date(2026, 9, 12), 25),
            ]
        }
        return screen_candidates(
            _settings(),
            [_line("Player")],
            logs,
            team_injuries={"NY": injuries},
        )

    def test_out_player_is_hard_excluded(self) -> None:
        result = self._screen([TeamInjury(player_name="Player", player_name_norm="player", team="NY", status="Out")])
        self.assertEqual([], result.candidates)
        self.assertEqual(1, result.excluded_unavailable_players)

    def test_suspended_and_injured_reserve_are_excluded(self) -> None:
        for status in ("Suspended", "Injured Reserve", "Out For Season"):
            result = self._screen([TeamInjury(player_name="Player", player_name_norm="player", team="NY", status=status)])
            self.assertEqual([], result.candidates, status)
            self.assertEqual(1, result.excluded_unavailable_players, status)

    def test_questionable_player_is_flagged_not_excluded(self) -> None:
        result = self._screen([TeamInjury(player_name="Player", player_name_norm="player", team="NY", status="Questionable")])
        self.assertEqual(1, len(result.candidates))
        self.assertIn("SELF_Q", result.candidates[0].flags)
        self.assertEqual(0, result.excluded_unavailable_players)

    def test_teammate_out_is_still_flagged(self) -> None:
        result = self._screen([TeamInjury(player_name="Teammate", player_name_norm="teammate", team="NY", status="Out", impact_level="team")])
        self.assertEqual(1, len(result.candidates))
        self.assertIn("TEAM_OUT", result.candidates[0].flags)


class DnpFeatureTests(unittest.TestCase):
    def test_dnp_rows_do_not_pollute_recent_averages(self) -> None:
        logs = {
            "player": [
                _log("Player", date(2026, 9, 16), 22),
                _log("Player", date(2026, 9, 15), 22),
                _log("Player", date(2026, 9, 14), 0, minutes=0.0, did_play=False),
                _log("Player", date(2026, 9, 13), 22),
                _log("Player", date(2026, 9, 12), 22),
                _log("Player", date(2026, 9, 11), 22),
            ]
        }
        result = screen_candidates(_settings(), [_line("Player", line=10.5)], logs)
        self.assertEqual(1, len(result.candidates))
        candidate = result.candidates[0]
        self.assertEqual(5, candidate.played_last_5)
        self.assertEqual(5, candidate.played_last_10)
        self.assertAlmostEqual(22.0, candidate.avg_last_5)


class TotalContextTests(unittest.TestCase):
    def test_wnba_scale_totals_flag_correctly(self) -> None:
        from wnba_props.screener import _context_flags

        self.assertEqual([], _context_flags("NY", None, None, total_high=172.0, total_low=156.0))

        class Context:
            away_team = "NY"
            home_team = "PHX"
            away_spread = -3.0
            home_spread = 3.0
            total = 175.0

        self.assertIn("HIGH_TOT", _context_flags("NY", Context(), None, total_high=172.0, total_low=156.0))
        Context.total = 150.0
        self.assertIn("LOW_TOT", _context_flags("NY", Context(), None, total_high=172.0, total_low=156.0))
        Context.total = 164.0
        self.assertNotIn("HIGH_TOT", _context_flags("NY", Context(), None, total_high=172.0, total_low=156.0))
        self.assertNotIn("LOW_TOT", _context_flags("NY", Context(), None, total_high=172.0, total_low=156.0))

    def test_settings_defaults_are_wnba_scale(self) -> None:
        settings = _settings()
        self.assertEqual(172.0, settings.total_context_high)
        self.assertEqual(156.0, settings.total_context_low)


class RunHealthTests(unittest.TestCase):
    def test_healthy_run(self) -> None:
        health = classify_run_health(
            eligible_games=3,
            matched_events=3,
            players_with_lines=10,
            players_loaded=10,
            evaluated_lines=40,
            degraded_reasons=[],
            settings=_settings(),
        )
        self.assertEqual("healthy", health.status)
        self.assertEqual([], health.reasons)

    def test_no_slate(self) -> None:
        health = classify_run_health(0, None, 0, 0, 0, [], _settings())
        self.assertEqual("no_slate", health.status)

    def test_degraded_on_player_coverage(self) -> None:
        health = classify_run_health(3, 3, 10, 8, 40, [], _settings())
        self.assertEqual("degraded", health.status)
        self.assertTrue(any("player log coverage" in reason for reason in health.reasons))

    def test_degraded_on_event_coverage(self) -> None:
        health = classify_run_health(3, 2, 10, 10, 40, [], _settings())
        self.assertEqual("degraded", health.status)
        self.assertTrue(any("event coverage" in reason for reason in health.reasons))

    def test_degraded_on_zero_evaluated_lines(self) -> None:
        health = classify_run_health(3, 3, 10, 10, 0, [], _settings())
        self.assertEqual("degraded", health.status)

    def test_degraded_on_source_failures(self) -> None:
        health = classify_run_health(3, 3, 10, 10, 40, ["injury source unavailable for NY"], _settings())
        self.assertEqual("degraded", health.status)


class PregameGuardTests(unittest.TestCase):
    def _game(self, game_id: str, hours_until_tip: float) -> Game:
        return Game(
            game_id=game_id,
            game_date=SCREEN_DATE,
            game_time=datetime.now(timezone.utc) + timedelta(hours=hours_until_tip),
            home_team="NY",
            away_team="PHX",
            source="test",
        )

    def _candidate(self, player: str, team: str = "NY", opponent: str = "PHX") -> Candidate:
        return Candidate(
            player_name=player,
            team=team,
            opponent=opponent,
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
            delta_avg_last_5=1.5,
            score=8,
        )

    def _prop_line(self, player: str, collected_minutes_ago: float) -> PropLine:
        return PropLine(
            event_id="1",
            game_date=SCREEN_DATE,
            player_name_raw=player,
            player_name_norm=player.lower(),
            team="NY",
            opponent="PHX",
            prop_type="PTS",
            line=19.5,
            bookmaker="fanduel",
            source="test",
            collected_at=datetime.now(timezone.utc) - timedelta(minutes=collected_minutes_ago),
        )

    def test_started_game_is_dropped_with_its_candidates(self) -> None:
        started = self._game("g1", hours_until_tip=-1.0)
        candidate = self._candidate("Player")
        result = apply_pregame_guard([started], [candidate], [], datetime.now(timezone.utc), 240)
        self.assertEqual([], result.games)
        self.assertEqual([], result.candidates)
        self.assertTrue(any("started" in note for note in result.notes))

    def test_stale_line_candidates_are_dropped(self) -> None:
        game = self._game("g1", hours_until_tip=4.0)
        fresh = self._candidate("Fresh Player")
        stale = self._candidate("Stale Player")
        lines = [self._prop_line("Fresh Player", 30.0), self._prop_line("Stale Player", 600.0)]
        result = apply_pregame_guard([game], [fresh, stale], lines, datetime.now(timezone.utc), 240)
        self.assertEqual(1, len(result.candidates))
        self.assertEqual("Fresh Player", result.candidates[0].player_name)
        self.assertTrue(any("older than" in note for note in result.notes))


class ArtifactOrderingTests(unittest.TestCase):
    def test_export_run_history_is_atomic_and_validated(self) -> None:
        payload = {
            "mode": "screen",
            "screen_date": "2026-09-17",
            "candidates": [{"player_name": "A"}],
        }
        path = export_run_history("test_artifact", payload)
        try:
            loaded = json.loads(path.read_text())
            self.assertEqual("2026-09-17", loaded["screen_date"])
            self.assertEqual(1, len(loaded["candidates"]))
            self.assertFalse(path.with_suffix(".tmp").exists())
        finally:
            path.unlink(missing_ok=True)

    def test_delivery_status_written(self) -> None:
        payload = {"mode": "screen", "screen_date": "2026-09-17", "candidates": []}
        path = export_run_history("test_delivery", payload)
        try:
            status_path = write_delivery_status(path, "sent")
            loaded = json.loads(status_path.read_text())
            self.assertEqual("sent", loaded["status"])
            self.assertEqual(path.name, loaded["artifact"])
        finally:
            path.unlink(missing_ok=True)
            status_path.unlink(missing_ok=True)


class ProvenanceTests(unittest.TestCase):
    def test_provenance_contains_identity_fields(self) -> None:
        provenance = run_provenance(_settings())
        self.assertIn("policy_version", provenance)
        self.assertIn("git_commit", provenance)
        self.assertIn("git_dirty", provenance)
        self.assertIn("config_fingerprint", provenance)
        self.assertNotIn("discord_webhook_url", json.dumps(provenance))
        self.assertEqual(16, len(provenance["config_fingerprint"]))


if __name__ == "__main__":
    unittest.main()
