from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from shutil import rmtree
from statistics import mean

from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from wnba_props.notifiers.discord import send_discord_embeds
from wnba_props.output import render_candidates, render_discord_embeds, render_line_board
from wnba_props.screener import screen_candidates, summarize_return_context
from wnba_props.sources.basketball_reference import BasketballReferenceSource
from wnba_props.sources.draftkings import DraftKingsSource
from wnba_props.sources.espn import EspnSlateSource
from wnba_props.sources.espn_gamelog import EspnGameLogSource
from wnba_props.sources.espn_injuries import EspnInjurySource
from wnba_props.sources.espn_odds import EspnOddsSource
from wnba_props.sources.espn_recent_boxscore_logs import EspnRecentBoxscoreLogsSource
from wnba_props.sources.fanduel import FanDuelSource
from wnba_props.sources.manual_lines import ManualLineSource
from wnba_props.sources.playerprops import PlayerPropsSource
from wnba_props.sources.propcruncher import PropCruncherSource
from wnba_props.utils import normalize_name


def season_end_year(screen_date: date) -> int:
    return screen_date.year


def _is_playoff_window(screen_date: date) -> bool:
    return screen_date.month in {9, 10}


def _latest_log_date(logs: list) -> date | None:
    if not logs:
        return None
    return max(log.game_date for log in logs)


def _merge_logs(primary_logs: list, supplemental_logs: list) -> list:
    merged = {}
    for log in supplemental_logs + primary_logs:
        key = (log.game_date, log.team, log.opponent)
        if key not in merged:
            merged[key] = log
    return sorted(merged.values(), key=lambda item: item.game_date, reverse=True)


def _logs_are_stale_for_screen(screen_date: date, logs: list) -> bool:
    latest_date = _latest_log_date(logs)
    if latest_date is None:
        return True
    if not _is_playoff_window(screen_date):
        return latest_date < screen_date - timedelta(days=5)
    return latest_date < screen_date - timedelta(days=2)


def _load_logs_with_playoff_refresh(
    player_name: str,
    team: str,
    screen_date: date,
    season: int,
    logs_source: BasketballReferenceSource,
    fallback_logs_source: EspnGameLogSource,
) -> list:
    logs = logs_source.fetch_logs(player_name, team, season)
    if not _is_playoff_window(screen_date):
        return logs

    latest_date = _latest_log_date(logs)
    if latest_date is not None and latest_date >= screen_date - timedelta(days=2):
        return logs

    fallback_logs = fallback_logs_source.fetch_logs(player_name, team, season)
    if not fallback_logs:
        return logs
    return _merge_logs(logs, fallback_logs)


def _refresh_logs_if_stale(
    player_name: str,
    team: str,
    screen_date: date,
    season: int,
    current_logs: list,
    refresh_logs_source: BasketballReferenceSource,
    refresh_fallback_logs_source: EspnGameLogSource,
    recent_boxscore_logs_source: EspnRecentBoxscoreLogsSource,
) -> list:
    if not _logs_are_stale_for_screen(screen_date, current_logs):
        return current_logs
    try:
        refreshed_logs = _load_logs_with_playoff_refresh(
            player_name=player_name,
            team=team,
            screen_date=screen_date,
            season=season,
            logs_source=refresh_logs_source,
            fallback_logs_source=refresh_fallback_logs_source,
        )
        if refreshed_logs and not _logs_are_stale_for_screen(screen_date, refreshed_logs):
            return refreshed_logs
    except Exception:
        pass
    try:
        recent_logs = recent_boxscore_logs_source.fetch_recent_logs(
            player_name=player_name,
            team_abbr=team,
            end_date=screen_date,
        )
        if recent_logs:
            merged_logs = _merge_logs(current_logs, recent_logs)
            if not _logs_are_stale_for_screen(screen_date, merged_logs):
                return merged_logs
    except Exception:
        pass
    return current_logs


def _load_recent_boxscore_logs(
    player_name: str,
    team: str,
    screen_date: date,
    recent_boxscore_logs_source: EspnRecentBoxscoreLogsSource,
) -> list:
    try:
        return recent_boxscore_logs_source.fetch_recent_logs(
            player_name=player_name,
            team_abbr=team,
            end_date=screen_date,
        )
    except Exception:
        return []


def _should_skip_expensive_recent_fallback(*exceptions: Exception) -> bool:
    text = " ".join(str(exc) for exc in exceptions).lower()
    throttle_or_challenge_markers = [
        "http 429",
        "too many requests",
        "waf challenge",
        "bot challenge",
        "verify that you're not a robot",
    ]
    return any(marker in text for marker in throttle_or_challenge_markers)


def _load_player_logs(
    player_name: str,
    team: str,
    screen_date: date,
    season: int,
    logs_source: BasketballReferenceSource,
    fallback_logs_source: EspnGameLogSource,
    refresh_logs_source: BasketballReferenceSource,
    refresh_fallback_logs_source: EspnGameLogSource,
    recent_boxscore_logs_source: EspnRecentBoxscoreLogsSource,
) -> tuple[list, str | None]:
    try:
        logs = _load_logs_with_playoff_refresh(
            player_name=player_name,
            team=team,
            screen_date=screen_date,
            season=season,
            logs_source=logs_source,
            fallback_logs_source=fallback_logs_source,
        )
        logs = _refresh_logs_if_stale(
            player_name=player_name,
            team=team,
            screen_date=screen_date,
            season=season,
            current_logs=logs,
            refresh_logs_source=refresh_logs_source,
            refresh_fallback_logs_source=refresh_fallback_logs_source,
            recent_boxscore_logs_source=recent_boxscore_logs_source,
        )
        if _logs_are_stale_for_screen(screen_date, logs):
            return [], f"{player_name}: stale recent logs for {screen_date.isoformat()}"
        return logs, None
    except Exception as exc:  # noqa: BLE001
        try:
            logs = fallback_logs_source.fetch_logs(player_name, team, season)
            logs = _refresh_logs_if_stale(
                player_name=player_name,
                team=team,
                screen_date=screen_date,
                season=season,
                current_logs=logs,
                refresh_logs_source=refresh_logs_source,
                refresh_fallback_logs_source=refresh_fallback_logs_source,
                recent_boxscore_logs_source=recent_boxscore_logs_source,
            )
            if _logs_are_stale_for_screen(screen_date, logs):
                recent_boxscore_logs = _load_recent_boxscore_logs(
                    player_name=player_name,
                    team=team,
                    screen_date=screen_date,
                    recent_boxscore_logs_source=recent_boxscore_logs_source,
                )
                if not _logs_are_stale_for_screen(screen_date, recent_boxscore_logs):
                    return recent_boxscore_logs, None
                return [], f"{player_name}: stale recent logs for {screen_date.isoformat()}"
            return logs, None
        except Exception as fallback_exc:  # noqa: BLE001
            if _should_skip_expensive_recent_fallback(exc, fallback_exc):
                return [], f"{player_name}: {exc}; fallback failed: {fallback_exc}"
            recent_boxscore_logs = _load_recent_boxscore_logs(
                player_name=player_name,
                team=team,
                screen_date=screen_date,
                recent_boxscore_logs_source=recent_boxscore_logs_source,
            )
            if not _logs_are_stale_for_screen(screen_date, recent_boxscore_logs):
                return recent_boxscore_logs, None
            return [], f"{player_name}: {exc}; fallback failed: {fallback_exc}"


def warm_cache_mode_enabled() -> bool:
    return "--warm-cache" in sys.argv


def cache_report_mode_enabled() -> bool:
    return "--cache-report" in sys.argv


def cache_cleanup_mode_enabled() -> bool:
    return "--cache-clean" in sys.argv


def print_cache_report() -> None:
    shared_dir = CACHE_DIR / "shared"
    lines_dir = CACHE_DIR / "lines"
    legacy_files = sorted(path for path in CACHE_DIR.glob("*.json") if path.is_file())

    def summarize(directory: Path) -> tuple[int, int]:
        files = sorted(path for path in directory.glob("*.json") if path.is_file())
        size = sum(path.stat().st_size for path in files)
        return len(files), size

    shared_count, shared_size = summarize(shared_dir)
    lines_count, lines_size = summarize(lines_dir)
    legacy_size = sum(path.stat().st_size for path in legacy_files)

    print("Cache report:")
    print(f"- Shared cache files: {shared_count} ({shared_size / 1024:.1f} KB)")
    print(f"- Lines cache files: {lines_count} ({lines_size / 1024:.1f} KB)")
    print(f"- Legacy top-level cache files: {len(legacy_files)} ({legacy_size / 1024:.1f} KB)")
    if legacy_files:
        print("- Legacy file examples:")
        for path in legacy_files[:10]:
            print(f"  - {path.name}")
        if len(legacy_files) > 10:
            print(f"  - ... and {len(legacy_files) - 10} more")


def clean_legacy_cache_files() -> int:
    legacy_files = sorted(path for path in CACHE_DIR.glob("*.json") if path.is_file())
    for path in legacy_files:
        path.unlink(missing_ok=True)
    return len(legacy_files)


def export_run_history(filename_prefix: str, payload: dict) -> Path:
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"{filename_prefix}_{timestamp}.json"
    path.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True))
    return path


def _classify_team_injury_impacts(team_injuries: dict[str, list], logs_by_player: dict[str, list]) -> None:
    for injuries in team_injuries.values():
        for injury in injuries:
            logs = logs_by_player.get(injury.player_name_norm, [])
            if not logs:
                continue
            recent_logs = logs[:10]
            injury.avg_minutes_last_10 = mean(log.minutes for log in recent_logs)
            injury.season_points = mean(log.points for log in logs)
            injury.season_rebounds = mean(log.rebounds for log in logs)
            injury.season_assists = mean(log.assists for log in logs)
            injury.impact_score = (
                0.40 * injury.avg_minutes_last_10
                + 0.35 * injury.season_points
                + 0.50 * injury.season_assists
                + 0.15 * injury.season_rebounds
            )

        ranked = sorted(
            [injury for injury in injuries if injury.impact_score is not None],
            key=lambda injury: injury.impact_score or 0.0,
            reverse=True,
        )
        for injury in injuries:
            injury.impact_level = "minor"
        for index, injury in enumerate(ranked):
            if index <= 1 and (injury.impact_score or 0.0) >= 18.0:
                injury.impact_level = "key"
            elif index <= 4 and (injury.impact_score or 0.0) >= 13.0:
                injury.impact_level = "team"


def main() -> int:
    settings = load_settings()
    warm_cache_only = warm_cache_mode_enabled()
    cache_report_only = cache_report_mode_enabled()
    cache_cleanup_only = cache_cleanup_mode_enabled()

    if cache_cleanup_only:
        removed = clean_legacy_cache_files()
        print(f"Removed {removed} legacy top-level cache files from {CACHE_DIR}.")
        return 0

    if cache_report_only:
        print_cache_report()
        return 0

    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(CACHE_DIR / "lines", ttl_hours=settings.lines_cache_ttl_minutes / 60.0)
    injuries_cache = JsonCache(CACHE_DIR / "injuries", ttl_hours=settings.injuries_cache_ttl_minutes / 60.0)
    slate_source = EspnSlateSource()
    if settings.line_source == "playerprops":
        line_source = PlayerPropsSource(settings, lines_cache)
    elif settings.line_source == "propcruncher":
        line_source = PropCruncherSource(settings, shared_cache)
    elif settings.line_source == "draftkings":
        line_source = DraftKingsSource(settings, lines_cache)
    elif settings.line_source == "fanduel":
        line_source = FanDuelSource(settings, shared_cache, lines_cache)
    elif settings.line_source == "manual":
        line_source = ManualLineSource()
    else:
        print("Supported LINE_SOURCE values: playerprops, draftkings, propcruncher, fanduel, manual.", file=sys.stderr)
        return 1
    logs_source = BasketballReferenceSource(shared_cache, sticky_daily_cache=settings.sticky_daily_log_cache)
    fallback_logs_source = EspnGameLogSource(shared_cache)
    refresh_root = CACHE_DIR / "run_refresh"
    if refresh_root.exists():
        rmtree(refresh_root)
    refresh_cache = JsonCache(refresh_root, ttl_hours=settings.cache_ttl_hours)
    refresh_logs_source = BasketballReferenceSource(refresh_cache, sticky_daily_cache=False)
    refresh_fallback_logs_source = EspnGameLogSource(refresh_cache)
    recent_boxscore_logs_source = EspnRecentBoxscoreLogsSource(refresh_cache)
    odds_context_source = EspnOddsSource(shared_cache)
    injury_source = EspnInjurySource(injuries_cache)

    try:
        games = slate_source.fetch_games(settings.screen_date)
        if settings.pregame_only:
            now_utc = datetime.now(timezone.utc)
            games = [game for game in games if game.game_time > now_utc]
        if not games:
            print(f"No eligible WNBA games found for {settings.screen_date.isoformat()}.", file=sys.stderr)
            return 0

        prop_lines = line_source.fetch_prop_lines(games)
        if not prop_lines:
            print("No supported prop lines found.", file=sys.stderr)
            if getattr(line_source, "failures", None):
                print("Line-source issues:", file=sys.stderr)
                for failure in line_source.failures[:20]:
                    print(f"- {failure}", file=sys.stderr)
                if len(line_source.failures) > 20:
                    print(f"- ... and {len(line_source.failures) - 20} more", file=sys.stderr)
            return 1
        try:
            game_contexts = odds_context_source.fetch_game_context(settings.screen_date)
        except Exception:
            game_contexts = {}
        team_injuries = {}
        for team in sorted({game.home_team for game in games} | {game.away_team for game in games}):
            try:
                injuries = injury_source.fetch_team_injuries(team, settings.screen_date)
            except Exception:
                injuries = []
            if injuries:
                team_injuries[team] = injuries

        logs_by_player = {}
        target_season = season_end_year(settings.screen_date)
        failures = set()
        target_players = []
        seen_player_keys = set()
        for line in prop_lines:
            player_name = settings.player_aliases.get(line.player_name_raw, line.player_name_raw)
            player_key = normalize_name(player_name)
            if player_key in seen_player_keys:
                continue
            seen_player_keys.add(player_key)
            target_players.append((player_name, player_key, line.team))
        unique_player_keys_with_lines = set(seen_player_keys)

        for index, (player_name, player_key, team) in enumerate(target_players, start=1):
            if player_key in logs_by_player:
                continue
            print(
                f"Loading player logs {index}/{len(target_players)}: {player_name} ({team})",
                file=sys.stderr,
                flush=True,
            )
            loaded_logs, failure_reason = _load_player_logs(
                player_name=player_name,
                team=team,
                screen_date=settings.screen_date,
                season=target_season,
                logs_source=logs_source,
                fallback_logs_source=fallback_logs_source,
                refresh_logs_source=refresh_logs_source,
                refresh_fallback_logs_source=refresh_fallback_logs_source,
                recent_boxscore_logs_source=recent_boxscore_logs_source,
            )
            if loaded_logs:
                logs_by_player[player_key] = loaded_logs
                continue
            if failure_reason:
                failures.add(failure_reason)

        for injuries in team_injuries.values():
            for injury in injuries:
                if injury.player_name_norm in logs_by_player:
                    continue
                loaded_logs, _ = _load_player_logs(
                    player_name=injury.player_name,
                    team=injury.team,
                    screen_date=settings.screen_date,
                    season=target_season,
                    logs_source=logs_source,
                    fallback_logs_source=fallback_logs_source,
                    refresh_logs_source=refresh_logs_source,
                    refresh_fallback_logs_source=refresh_fallback_logs_source,
                    recent_boxscore_logs_source=recent_boxscore_logs_source,
                )
                if loaded_logs:
                    logs_by_player[injury.player_name_norm] = loaded_logs

        _classify_team_injury_impacts(team_injuries, logs_by_player)

        if warm_cache_only:
            print("Cache warm-up summary:")
            print(f"- Unique players with lines: {len(unique_player_keys_with_lines)}")
            print(f"- Players loaded successfully: {len([key for key in unique_player_keys_with_lines if key in logs_by_player])}")
            print(f"- Same-day cache hits: {logs_source.stats.same_day_cache_hits}")
            print(f"- Fresh cache hits: {logs_source.stats.ttl_cache_hits}")
            print(f"- Fresh Basketball-Reference fetches: {logs_source.stats.fresh_fetches}")
            print(f"- Stale cache fallbacks after 429: {logs_source.stats.stale_cache_fallbacks}")
            print(f"- Players skipped for data issues: {len(failures)}")
            if failures:
                print("")
                print("Skipped players (data issues):")
                sorted_failures = sorted(failures)
                for failure in sorted_failures[:20]:
                    print(f"- {failure}")
                if len(sorted_failures) > 20:
                    print(f"- ... and {len(sorted_failures) - 20} more")
            if settings.export_history:
                export_path = export_run_history(
                    "warm_cache",
                    {
                        "mode": "warm_cache",
                        "screen_date": settings.screen_date.isoformat(),
                        "summary": {
                            "unique_players_with_lines": len({normalize_name(line.player_name_raw) for line in prop_lines}),
                            "players_loaded_successfully": len([key for key in unique_player_keys_with_lines if key in logs_by_player]),
                            "same_day_cache_hits": logs_source.stats.same_day_cache_hits,
                            "fresh_cache_hits": logs_source.stats.ttl_cache_hits,
                            "fresh_bref_fetches": logs_source.stats.fresh_fetches,
                            "stale_cache_fallbacks": logs_source.stats.stale_cache_fallbacks,
                            "players_skipped_for_data_issues": len(failures),
                        },
                        "failures": sorted(failures),
                    },
                )
                print(f"- History exported to {export_path}")
            return 0

        screening_result = screen_candidates(settings, prop_lines, logs_by_player, game_contexts=game_contexts, team_injuries=team_injuries)
        displayed_candidates = [candidate for candidate in screening_result.candidates if candidate.score >= settings.min_display_score]
        hidden_candidates = len(screening_result.candidates) - len(displayed_candidates)
        return_context = summarize_return_context(
            displayed_candidates,
            logs_by_player,
            team_injuries,
            settings.screen_date,
        )
        print(
            render_candidates(
                screening_result.candidates,
                min_score=settings.min_display_score,
                team_injuries=team_injuries,
                return_context=return_context,
            )
        )
        if not displayed_candidates:
            print("")
            print("Evaluated Lines:")
            print(render_line_board(prop_lines, logs_by_player))
        print("")
        print("Run summary:")
        print(f"- Unique players with lines: {len(unique_player_keys_with_lines)}")
        print(f"- Players loaded successfully: {len([key for key in unique_player_keys_with_lines if key in logs_by_player])}")
        print(f"- Same-day cache hits: {logs_source.stats.same_day_cache_hits}")
        print(f"- Fresh cache hits: {logs_source.stats.ttl_cache_hits}")
        print(f"- Fresh Basketball-Reference fetches: {logs_source.stats.fresh_fetches}")
        print(f"- Stale cache fallbacks after 429: {logs_source.stats.stale_cache_fallbacks}")
        print(f"- Players skipped for data issues: {len(failures)}")
        print(f"- Prop lines evaluated: {screening_result.evaluated_prop_lines}")
        print(f"- Prop lines that qualified: {len(screening_result.candidates)}")
        print(f"- Prop lines displayed (score >= {settings.min_display_score}): {len(displayed_candidates)}")
        print(f"- Qualified props hidden below display threshold: {hidden_candidates}")
        print(f"- Prop lines evaluated but not qualified: {screening_result.non_qualifying_prop_lines}")
        if getattr(line_source, "failures", None):
            print("")
            print("Line-source issues:")
            for failure in line_source.failures[:20]:
                print(f"- {failure}")
            if len(line_source.failures) > 20:
                print(f"- ... and {len(line_source.failures) - 20} more")

        if settings.send_discord:
            embeds = render_discord_embeds(
                screening_result.candidates,
                screen_date=settings.screen_date,
                games_count=len(games),
                prop_line_count=len(prop_lines),
                qualified_count=len(screening_result.candidates),
                displayed_count=len(displayed_candidates),
                line_source=settings.line_source,
                bookmaker=settings.playerprops_book if settings.line_source == "playerprops" else settings.line_source.upper(),
                min_score=settings.discord_min_score,
                limit=settings.discord_limit,
            )
            discord_result = send_discord_embeds(settings.discord_webhook_url, embeds)
            if discord_result.ok:
                print("- Discord notification: sent")
            else:
                print(f"- Discord notification: failed ({discord_result.error or discord_result.status_code})")

        backtest_export_path = export_run_history(
            "screen_run",
            {
                "mode": "screen",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "screen_date": settings.screen_date.isoformat(),
                "games": [asdict(game) for game in games],
                "prop_lines": [asdict(line) for line in prop_lines],
                "summary": {
                    "unique_players_with_lines": len({normalize_name(line.player_name_raw) for line in prop_lines}),
                    "players_loaded_successfully": len([key for key in unique_player_keys_with_lines if key in logs_by_player]),
                    "same_day_cache_hits": logs_source.stats.same_day_cache_hits,
                    "fresh_cache_hits": logs_source.stats.ttl_cache_hits,
                    "fresh_bref_fetches": logs_source.stats.fresh_fetches,
                    "stale_cache_fallbacks": logs_source.stats.stale_cache_fallbacks,
                    "players_skipped_for_data_issues": len(failures),
                    "prop_lines_evaluated": screening_result.evaluated_prop_lines,
                    "prop_lines_qualified": len(screening_result.candidates),
                    "prop_lines_not_qualified": screening_result.non_qualifying_prop_lines,
                    "prop_lines_displayed": len(displayed_candidates),
                    "qualified_hidden_below_threshold": hidden_candidates,
                },
                "candidates": [asdict(candidate) for candidate in screening_result.candidates],
                "failures": sorted(failures),
                "line_source_failures": list(getattr(line_source, "failures", [])),
            },
        )
        print(f"- History exported to {backtest_export_path}")

        if failures:
            print("")
            print("Skipped players (data issues):")
            sorted_failures = sorted(failures)
            for failure in sorted_failures[:20]:
                print(f"- {failure}")
            if len(sorted_failures) > 20:
                print(f"- ... and {len(sorted_failures) - 20} more")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
