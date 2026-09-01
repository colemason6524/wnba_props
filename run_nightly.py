from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from shutil import rmtree
from statistics import mean

from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from wnba_props.notifiers.discord import send_discord_embeds, send_discord_message
from wnba_props.output import render_candidates, render_discord_embeds, render_line_board
from wnba_props.screener import filter_logs_as_of, screen_candidates, summarize_return_context
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
    return latest_date < screen_date - timedelta(days=_max_log_age_days(screen_date))


def _max_log_age_days(screen_date: date) -> int:
    if _is_playoff_window(screen_date):
        return int(os.environ.get("PLAYOFF_LOG_STALE_DAYS", "2"))
    return int(os.environ.get("REGULAR_SEASON_LOG_STALE_DAYS", "21"))


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


def _line_source_failure_message(settings, games: list, line_source) -> str:
    diagnostics = getattr(line_source, "diagnostics", {}) or {}
    failures = list(getattr(line_source, "failures", []) or [])
    details = [
        "WNBA props data failure",
        f"Date: {settings.screen_date.isoformat()}",
        f"Source: {settings.line_source}",
        f"Requested book: {settings.playerprops_book if settings.line_source == 'playerprops' else '-'}",
        f"Eligible ESPN games: {len(games)}",
    ]
    if diagnostics:
        details.extend(
            [
                f"PlayerProps events: {diagnostics.get('payload_events', '-')}",
                f"Matched events: {diagnostics.get('matched_events', '-')}",
                f"Selected-book plays inspected: {diagnostics.get('selected_book_plays', '-')}",
            ]
        )
    if failures:
        details.append("Issues:")
        details.extend(f"- {failure}" for failure in failures[:5])
    details.append("The run exited without producing a picks board. This is not a normal no-plays result.")
    return "\n".join(details)


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


@dataclass
class RunHealth:
    status: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class PregameGuardResult:
    games: list
    candidates: list
    notes: list[str] = field(default_factory=list)


def classify_run_health(
    eligible_games: int,
    matched_events: int | None,
    players_with_lines: int,
    players_loaded: int,
    evaluated_lines: int,
    degraded_reasons: list[str],
    settings,
) -> RunHealth:
    reasons = list(degraded_reasons)
    if eligible_games == 0:
        return RunHealth(status="no_slate", reasons=reasons)
    if matched_events is not None and matched_events / eligible_games < settings.min_event_match_ratio:
        reasons.append(
            f"event coverage {matched_events}/{eligible_games} below required ratio {settings.min_event_match_ratio:.2f}"
        )
    if players_with_lines and players_loaded / players_with_lines < settings.min_player_load_ratio:
        reasons.append(
            f"player log coverage {players_loaded}/{players_with_lines} below required ratio {settings.min_player_load_ratio:.2f}"
        )
    if evaluated_lines < settings.min_evaluated_lines:
        reasons.append(
            f"evaluated lines {evaluated_lines} below minimum {settings.min_evaluated_lines}"
        )
    return RunHealth(status="degraded" if reasons else "healthy", reasons=reasons)


def apply_pregame_guard(
    games: list,
    candidates: list,
    prop_lines: list,
    now_utc: datetime,
    max_line_age_minutes: int,
) -> PregameGuardResult:
    kept_games = list(games)
    kept_candidates = list(candidates)
    notes: list[str] = []

    started = [game for game in kept_games if game.game_time <= now_utc]
    if started:
        started_pairs = {frozenset((game.home_team, game.away_team)) for game in started}
        kept_games = [game for game in kept_games if frozenset((game.home_team, game.away_team)) not in started_pairs]
        kept_candidates = [
            candidate
            for candidate in kept_candidates
            if frozenset((candidate.team, candidate.opponent)) not in started_pairs
        ]
        notes.append(f"dropped {len(started)} game(s) that started during the run")

    collected_by_key: dict[tuple[str, str, float], datetime] = {}
    for line in prop_lines:
        key = (normalize_name(line.player_name_raw), line.prop_type, round(float(line.line), 1))
        previous = collected_by_key.get(key)
        if previous is None or line.collected_at > previous:
            collected_by_key[key] = line.collected_at
    stale_count = 0
    fresh_candidates: list = []
    for candidate in kept_candidates:
        collected = collected_by_key.get(
            (normalize_name(candidate.player_name), candidate.prop_type, round(float(candidate.line), 1))
        )
        if collected is not None and (now_utc - collected).total_seconds() / 60.0 > max_line_age_minutes:
            stale_count += 1
            continue
        fresh_candidates.append(candidate)
    if stale_count:
        notes.append(f"dropped {stale_count} candidate(s) with lines older than {max_line_age_minutes} minutes")
    return PregameGuardResult(games=kept_games, candidates=fresh_candidates, notes=notes)


def run_provenance(settings) -> dict:
    commit = None
    dirty = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        status_output = subprocess.run(
            ["git", "status", "--porcelain", "-uno"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(status_output)
    except (OSError, subprocess.SubprocessError):
        pass
    settings_view = {
        key: value
        for key, value in asdict(settings).items()
        if key != "discord_webhook_url"
    }
    fingerprint = hashlib.sha256(
        json.dumps(settings_view, default=str, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "policy_version": "prod-2026-08-31-safety",
        "git_commit": commit,
        "git_dirty": dirty,
        "python_version": sys.version.split()[0],
        "config_fingerprint": fingerprint,
        "thresholds": asdict(settings.thresholds),
        "discord_min_score": settings.discord_min_score,
        "discord_suppress_flags": ["SEASON-", "TEAM_OUT"],
        "discord_limit": settings.discord_limit,
        "total_context_high": settings.total_context_high,
        "total_context_low": settings.total_context_low,
    }


def record_run_health(payload: dict) -> Path:
    health_dir = OUTPUTS_DIR / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    path = health_dir / "run_status.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str, sort_keys=True) + "\n")
    return path


def export_run_history(filename_prefix: str, payload: dict) -> Path:
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"{filename_prefix}_{timestamp}.json"
    serialized = json.dumps(payload, default=str, indent=2, sort_keys=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(serialized)
    reloaded = json.loads(tmp_path.read_text())
    if reloaded.get("screen_date") != payload.get("screen_date"):
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("history artifact readback validation failed")
    if len(reloaded.get("candidates", [])) != len(payload.get("candidates", [])):
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("history artifact candidate readback mismatch")
    os.replace(tmp_path, path)
    return path


def write_delivery_status(history_path: Path, status: str, detail: str = "") -> Path:
    payload = {
        "artifact": history_path.name,
        "status": status,
        "detail": detail,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = history_path.with_name(history_path.stem + ".delivery.json")
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp_path, path)
    return path


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
            record_run_health(
                {
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "screen_date": settings.screen_date.isoformat(),
                    "status": "no_slate",
                    "reasons": ["no eligible pregame games"],
                }
            )
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
            diagnostics = dict(getattr(line_source, "diagnostics", {}) or {})
            failure_path = export_run_history(
                "screen_failure",
                {
                    "mode": "screen_failure",
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "screen_date": settings.screen_date.isoformat(),
                    "games": [asdict(game) for game in games],
                    "line_source": settings.line_source,
                    "bookmaker": settings.playerprops_book if settings.line_source == "playerprops" else "",
                    "diagnostics": diagnostics,
                    "failures": list(getattr(line_source, "failures", []) or []),
                },
            )
            print(f"- Failure snapshot exported to {failure_path}", file=sys.stderr)
            record_run_health(
                {
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "screen_date": settings.screen_date.isoformat(),
                    "status": "failed",
                    "reasons": ["no prop lines from source"],
                }
            )
            if settings.send_discord:
                discord_result = send_discord_message(
                    settings.discord_webhook_url,
                    _line_source_failure_message(settings, games, line_source),
                )
                if discord_result.ok:
                    print("- Discord data-failure notification: sent", file=sys.stderr)
                else:
                    print(
                        f"- Discord data-failure notification: failed "
                        f"({discord_result.error or discord_result.status_code})",
                        file=sys.stderr,
                    )
            return 1
        degraded_reasons: list[str] = []
        try:
            game_contexts = odds_context_source.fetch_game_context(settings.screen_date)
        except Exception as exc:  # noqa: BLE001
            game_contexts = {}
            degraded_reasons.append(f"odds context unavailable: {exc}")
        team_injuries = {}
        injury_failed_teams: list[str] = []
        for team in sorted({game.home_team for game in games} | {game.away_team for game in games}):
            try:
                injuries = injury_source.fetch_team_injuries(team, settings.screen_date)
            except Exception as exc:  # noqa: BLE001
                injuries = []
                injury_failed_teams.append(team)
                degraded_reasons.append(f"injury source unavailable for {team}: {exc}")
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

        logs_by_player = filter_logs_as_of(logs_by_player, settings.screen_date)
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
        now_utc = datetime.now(timezone.utc)
        if settings.pregame_only:
            guard = apply_pregame_guard(games, screening_result.candidates, prop_lines, now_utc, settings.max_line_age_minutes)
        else:
            guard = PregameGuardResult(games=games, candidates=screening_result.candidates, notes=[])
        kept_candidates = guard.candidates
        for note in guard.notes:
            print(f"- Pregame guard: {note}")
        displayed_candidates = [candidate for candidate in kept_candidates if candidate.score >= settings.min_display_score]
        hidden_candidates = len(kept_candidates) - len(displayed_candidates)
        return_context = summarize_return_context(
            kept_candidates,
            logs_by_player,
            team_injuries,
            settings.screen_date,
        )
        print(
            render_candidates(
                kept_candidates,
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
        matched_events = (getattr(line_source, "diagnostics", {}) or {}).get("matched_events")
        health = classify_run_health(
            eligible_games=len(games),
            matched_events=matched_events,
            players_with_lines=len(unique_player_keys_with_lines),
            players_loaded=len([key for key in unique_player_keys_with_lines if key in logs_by_player]),
            evaluated_lines=screening_result.evaluated_prop_lines,
            degraded_reasons=degraded_reasons + [note for note in guard.notes if "started" in note],
            settings=settings,
        )
        print("Run summary:")
        print(f"- Run health: {health.status.upper()}" + (f" ({'; '.join(health.reasons)})" if health.reasons else ""))
        print(f"- Unique players with lines: {len(unique_player_keys_with_lines)}")
        print(f"- Players loaded successfully: {len([key for key in unique_player_keys_with_lines if key in logs_by_player])}")
        print(f"- Same-day cache hits: {logs_source.stats.same_day_cache_hits}")
        print(f"- Fresh cache hits: {logs_source.stats.ttl_cache_hits}")
        print(f"- Fresh Basketball-Reference fetches: {logs_source.stats.fresh_fetches}")
        print(f"- Stale cache fallbacks after 429: {logs_source.stats.stale_cache_fallbacks}")
        print(f"- Players skipped for data issues: {len(failures)}")
        print(f"- Prop lines evaluated: {screening_result.evaluated_prop_lines}")
        print(f"- Players excluded as OUT/IR/suspended: {screening_result.excluded_unavailable_players}")
        print(f"- Prop lines that qualified: {len(kept_candidates)}")
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

        provenance = run_provenance(settings)
        history_payload = {
            "mode": "screen",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "screen_date": settings.screen_date.isoformat(),
            "games": [asdict(game) for game in guard.games],
            "prop_lines": [asdict(line) for line in prop_lines],
            "provenance": provenance,
            "health": {"status": health.status, "reasons": health.reasons},
            "summary": {
                "unique_players_with_lines": len({normalize_name(line.player_name_raw) for line in prop_lines}),
                "players_loaded_successfully": len([key for key in unique_player_keys_with_lines if key in logs_by_player]),
                "same_day_cache_hits": logs_source.stats.same_day_cache_hits,
                "fresh_cache_hits": logs_source.stats.ttl_cache_hits,
                "fresh_bref_fetches": logs_source.stats.fresh_fetches,
                "stale_cache_fallbacks": logs_source.stats.stale_cache_fallbacks,
                "players_skipped_for_data_issues": len(failures),
                "players_excluded_unavailable": screening_result.excluded_unavailable_players,
                "prop_lines_evaluated": screening_result.evaluated_prop_lines,
                "prop_lines_qualified": len(kept_candidates),
                "prop_lines_not_qualified": screening_result.non_qualifying_prop_lines,
                "prop_lines_displayed": len(displayed_candidates),
                "qualified_hidden_below_threshold": hidden_candidates,
                "run_health": health.status,
            },
            "candidates": [asdict(candidate) for candidate in kept_candidates],
            "failures": sorted(failures),
            "line_source_failures": list(getattr(line_source, "failures", [])),
        }
        history_path = export_run_history("screen_run", history_payload)
        print(f"- History exported to {history_path}")
        record_run_health(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "screen_date": settings.screen_date.isoformat(),
                "status": health.status,
                "reasons": health.reasons,
                "artifact": history_path.name,
                "git_commit": provenance.get("git_commit"),
                "git_dirty": provenance.get("git_dirty"),
                "counts": {
                    "players_with_lines": len(unique_player_keys_with_lines),
                    "players_loaded": len([key for key in unique_player_keys_with_lines if key in logs_by_player]),
                    "prop_lines_evaluated": screening_result.evaluated_prop_lines,
                    "candidates": len(kept_candidates),
                },
            }
        )

        if failures:
            print("")
            print("Skipped players (data issues):")
            sorted_failures = sorted(failures)
            for failure in sorted_failures[:20]:
                print(f"- {failure}")
            if len(sorted_failures) > 20:
                print(f"- ... and {len(sorted_failures) - 20} more")

        if settings.send_discord:
            blocked_reason = ""
            if health.status == "degraded" and not settings.allow_degraded_discord:
                blocked_reason = f"run health is degraded ({'; '.join(health.reasons)})"
            elif settings.require_clean_tree and provenance.get("git_dirty"):
                blocked_reason = "git working tree is dirty (WNBA_REQUIRE_CLEAN_TREE=false to override)"
            if blocked_reason:
                print(f"- Discord notification: BLOCKED - {blocked_reason}", file=sys.stderr)
                write_delivery_status(history_path, "blocked", blocked_reason)
            else:
                embeds = render_discord_embeds(
                    kept_candidates,
                    screen_date=settings.screen_date,
                    games_count=len(guard.games),
                    prop_line_count=len(prop_lines),
                    qualified_count=len(kept_candidates),
                    displayed_count=len(displayed_candidates),
                    line_source=settings.line_source,
                    bookmaker=settings.playerprops_book if settings.line_source == "playerprops" else settings.line_source.upper(),
                    min_score=settings.discord_min_score,
                    limit=settings.discord_limit,
                )
                discord_result = send_discord_embeds(settings.discord_webhook_url, embeds)
                if discord_result.ok:
                    print("- Discord notification: sent")
                    write_delivery_status(history_path, "sent")
                else:
                    print(f"- Discord notification: failed ({discord_result.error or discord_result.status_code})")
                    write_delivery_status(history_path, "failed", discord_result.error or str(discord_result.status_code))

        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Run failed: {exc}", file=sys.stderr)
        record_run_health(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "screen_date": getattr(load_settings(), "screen_date", date.today()).isoformat(),
                "status": "failed",
                "reasons": [str(exc)],
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
