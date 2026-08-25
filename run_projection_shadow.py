from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from run_nightly import _load_player_logs, season_end_year
from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR, ROOT, load_settings
from wnba_props.models import Game, PropLine
from wnba_props.shadow import MODEL_VERSION, ProjectionConfig, project_points_line
from wnba_props.shadow.calibration import ResidualArtifact, load_calibration_artifact
from wnba_props.shadow.collection import (
    CaptureRegistry,
    CaptureWindow,
    capture_lead_minutes,
    projection_id,
    select_games_in_capture_window,
)
from wnba_props.shadow.output import render_shadow_board
from wnba_props.shadow.projection import config_signature
from wnba_props.shadow.sources import ShadowEspnSlateSource, ShadowGameContext
from wnba_props.sources.basketball_reference import BasketballReferenceSource
from wnba_props.sources.espn_gamelog import EspnGameLogSource
from wnba_props.sources.espn_injuries import EspnInjurySource
from wnba_props.sources.espn_recent_boxscore_logs import EspnRecentBoxscoreLogsSource
from wnba_props.sources.manual_lines import ManualLineSource
from wnba_props.sources.playerprops import PlayerPropsSource
from wnba_props.utils import normalize_name

LINE_PROJECTED = "projected"
LINE_EXCLUDED = "excluded"
LINE_LOG_FAILURE = "log_failure"
LINE_NO_LOGS = "no_logs"
LINE_NO_GAME_MATCH = "no_game_match"

TRANSIENT_LINE_STATUSES = {LINE_LOG_FAILURE, LINE_NO_LOGS, LINE_NO_GAME_MATCH}


def main() -> int:
    args = _parse_args()
    settings = load_settings()
    if args.screen_date:
        settings.screen_date = date.fromisoformat(args.screen_date)
    settings.supported_prop_types = ["PTS"]

    config = ProjectionConfig(simulations=args.simulations)
    try:
        artifact_path = Path(args.calibration_artifact)
        if not artifact_path.is_absolute():
            artifact_path = ROOT / artifact_path
        residuals: ResidualArtifact = load_calibration_artifact(artifact_path)
    except ValueError as exc:
        print(f"Shadow run failed: {exc}", file=sys.stderr)
        return 1
    code_commit, code_dirty = _git_state()

    shadow_cache_root = CACHE_DIR / "shadow"
    shared_cache = JsonCache(shadow_cache_root / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(
        shadow_cache_root / "lines",
        ttl_hours=settings.lines_cache_ttl_minutes / 60.0,
    )
    injuries_cache = JsonCache(
        shadow_cache_root / "injuries",
        ttl_hours=settings.injuries_cache_ttl_minutes / 60.0,
    )

    line_source_name = os.environ.get("SHADOW_LINE_SOURCE", "playerprops").strip().lower()
    if line_source_name == "playerprops":
        line_source = PlayerPropsSource(settings, lines_cache)
    elif line_source_name == "manual":
        line_source = ManualLineSource()
    else:
        print("Supported SHADOW_LINE_SOURCE values: playerprops, manual.", file=sys.stderr)
        return 1

    health: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "screen_date": settings.screen_date.isoformat(),
        "model_version": MODEL_VERSION,
        "model_config_hash": config_signature(config),
        "residual_model_id": residuals.residual_model_id,
        "calibration_artifact_sha256": residuals.sha256,
        "line_source": line_source_name,
        "code_commit": code_commit,
        "code_dirty": code_dirty,
        "outcome": "error",
        "eligible_games": 0,
        "completed_games": [],
        "projections": 0,
        "both_side_priced": 0,
        "both_side_price_rate": None,
        "failures": [],
        "exit_code": 1,
    }

    capture_time = datetime.now(timezone.utc)
    registry: CaptureRegistry | None = None
    games: list[Game] = []
    attempt_recorded = False
    slate_source = ShadowEspnSlateSource()
    try:
        all_games = slate_source.fetch_games(settings.screen_date)
        capture_time = datetime.now(timezone.utc)
        capture_window = CaptureWindow(
            minimum_lead_minutes=args.capture_min_lead_minutes,
            maximum_lead_minutes=args.capture_max_lead_minutes,
        )
        registry = CaptureRegistry(shadow_cache_root / "state" / "capture_registry.json")
        games = _eligible_games(
            all_games,
            include_started=args.include_started,
            capture_time=capture_time,
            capture_window=capture_window,
            registry=registry,
            line_source=line_source_name,
            force_recapture=args.force_recapture,
        )
        health["eligible_games"] = len(games)
        if not games:
            health["outcome"] = "no_window"
            health["exit_code"] = 0
            _record_health(health)
            print(
                "No uncaptured WNBA games are currently inside the shadow capture window "
                f"({capture_window.minimum_lead_minutes:.0f}-"
                f"{capture_window.maximum_lead_minutes:.0f} minutes before tip)."
            )
            return 0

        prop_lines = [line for line in line_source.fetch_prop_lines(games) if line.prop_type == "PTS"]
        if not prop_lines:
            if not args.include_started:
                registry.record_attempt(
                    games=games,
                    model_version=MODEL_VERSION,
                    line_source=line_source_name,
                    snapshot_path=None,
                    captured_at=capture_time,
                    outcomes={game.game_id: "no_lines" for game in games},
                )
                attempt_recorded = True
            health["outcome"] = "no_lines"
            health["failures"] = sorted(set(getattr(line_source, "failures", [])))
            _record_health(health)
            print("No PTS lines found for the shadow run.", file=sys.stderr)
            return 1

        odds_contexts = slate_source.game_contexts
        injuries_by_team, injury_errors = _load_injuries(injuries_cache, games, settings.screen_date)
        statuses = {
            (team, injury.player_name_norm): injury.status
            for team, injuries in injuries_by_team.items()
            for injury in injuries
        }

        logs_source = BasketballReferenceSource(
            shared_cache,
            sticky_daily_cache=settings.sticky_daily_log_cache,
        )
        fallback_logs_source = EspnGameLogSource(shared_cache)
        failures: list[str] = list(getattr(line_source, "failures", []))
        projections = []
        game_by_id = {game.game_id: game for game in games}

        line_outcomes: dict[str, list[str]] = defaultdict(list)
        transient_by_game: dict[str, bool] = defaultdict(bool)

        with tempfile.TemporaryDirectory(prefix="wnba-shadow-refresh-") as refresh_dir:
            refresh_cache = JsonCache(Path(refresh_dir), ttl_hours=settings.cache_ttl_hours)
            refresh_logs_source = BasketballReferenceSource(refresh_cache, sticky_daily_cache=False)
            refresh_fallback_logs_source = EspnGameLogSource(refresh_cache)
            recent_boxscore_logs_source = EspnRecentBoxscoreLogsSource(refresh_cache)

            loaded_logs: dict[str, list] = {}
            for index, line in enumerate(prop_lines, start=1):
                player_name = settings.player_aliases.get(line.player_name_raw, line.player_name_raw)
                player_key = normalize_name(line.player_name_raw)
                failure = None
                if player_key not in loaded_logs:
                    print(
                        f"Loading shadow logs {index}/{len(prop_lines)}: {player_name} ({line.team})",
                        file=sys.stderr,
                        flush=True,
                    )
                    logs, failure = _load_player_logs(
                        player_name=player_name,
                        team=line.team,
                        screen_date=settings.screen_date,
                        season=season_end_year(settings.screen_date),
                        logs_source=logs_source,
                        fallback_logs_source=fallback_logs_source,
                        refresh_logs_source=refresh_logs_source,
                        refresh_fallback_logs_source=refresh_fallback_logs_source,
                        recent_boxscore_logs_source=recent_boxscore_logs_source,
                    )
                    if logs:
                        loaded_logs[player_key] = logs
                    elif failure:
                        failures.append(failure)
                logs = loaded_logs.get(player_key, [])
                if not logs:
                    if failure:
                        line_outcomes[line.event_id].append(LINE_LOG_FAILURE)
                        transient_by_game[line.event_id] = True
                    else:
                        line_outcomes[line.event_id].append(LINE_NO_LOGS)
                        transient_by_game[line.event_id] = True
                    continue

                game = game_by_id.get(line.event_id)
                if game is None:
                    failures.append(f"{line.player_name_raw}: no matching ESPN game")
                    line_outcomes[line.event_id].append(LINE_NO_GAME_MATCH)
                    transient_by_game[line.event_id] = True
                    continue
                game_context = _resolve_game_context(odds_contexts, line.team, line.opponent)
                projection = project_points_line(
                    line=line,
                    game_time=game.game_time,
                    logs=logs,
                    screen_date=settings.screen_date,
                    team_spread=_team_spread(line.team, game_context),
                    game_total=game_context.total if game_context else None,
                    player_status=statuses.get((line.team, player_key), ""),
                    config=config,
                    residuals=residuals,
                    injury_source_available=line.team not in injury_errors,
                )
                if projection is None:
                    failures.append(f"{line.player_name_raw}: insufficient eligible history or unavailable")
                    line_outcomes[line.event_id].append(LINE_EXCLUDED)
                    continue
                projections.append(projection)
                line_outcomes[line.event_id].append(LINE_PROJECTED)

        for team, error in injury_errors.items():
            failures.append(f"injuries unavailable for {team}: {error}")
            for game in games:
                if team in {game.home_team, game.away_team}:
                    transient_by_game[game.game_id] = True

        for game in games:
            if _collected_too_late(game, prop_lines, capture_window.minimum_lead_minutes):
                failures.append(f"{game.away_team}@{game.home_team}: lines collected below minimum lead")
                transient_by_game[game.game_id] = True

        print(render_shadow_board(projections))
        export_path = _export_shadow_snapshot(
            screen_date=settings.screen_date,
            games=games,
            prop_lines=prop_lines,
            projections=projections,
            game_contexts=odds_contexts,
            failures=sorted(set(failures)),
            config=config,
            line_source=line_source_name,
            line_source_diagnostics=dict(getattr(line_source, "diagnostics", {})),
            capture_window=capture_window,
            strict_pregame=not args.include_started,
            code_commit=code_commit,
            code_dirty=code_dirty,
            injury_errors=dict(injury_errors),
            residuals=residuals,
        )

        outcomes = {
            game.game_id: _game_outcome(line_outcomes.get(game.game_id, []), transient_by_game[game.game_id])
            for game in games
        }
        complete_games = [game for game in games if outcomes[game.game_id] == "complete"]

        if not args.include_started:
            registry.record_attempt(
                games=games,
                model_version=MODEL_VERSION,
                line_source=line_source_name,
                snapshot_path=export_path,
                captured_at=capture_time,
                outcomes=outcomes,
            )
            attempt_recorded = True
            if complete_games:
                registry.mark_complete(
                    games=complete_games,
                    model_version=MODEL_VERSION,
                    line_source=line_source_name,
                    snapshot_path=export_path,
                    captured_at=capture_time,
                )

        both_side_priced = sum(1 for item in projections if item.price_status == "BOTH_SIDES_PRICED")
        health.update(
            {
                "outcome": _rollup_outcome(outcomes),
                "completed_games": [game.game_id for game in complete_games],
                "projections": len(projections),
                "both_side_priced": both_side_priced,
                "both_side_price_rate": round(both_side_priced / len(projections), 4) if projections else None,
                "failures": sorted(set(failures)),
                "exit_code": 0,
            }
        )
        _record_health(health)

        print("")
        print("Shadow summary:")
        print(f"- Eligible games: {len(games)}")
        print(f"- PTS lines: {len(prop_lines)}")
        print(f"- Projections produced: {len(projections)}")
        print(f"- Both-side prices: {both_side_priced}")
        print(f"- Games with spread/total context: {len(odds_contexts)}")
        print(f"- Research-only output: {export_path}")
        print(f"- Game outcomes: {outcomes}")
        if failures:
            print(f"- Data issues: {len(set(failures))}")
        return 0
    except Exception as exc:  # noqa: BLE001
        failures = [f"{type(exc).__name__}: {exc}"]
        if registry is not None and games and not args.include_started and not attempt_recorded:
            try:
                registry.record_attempt(
                    games=games,
                    model_version=MODEL_VERSION,
                    line_source=line_source_name,
                    snapshot_path=None,
                    captured_at=capture_time,
                    outcomes={game.game_id: "failed" for game in games},
                )
            except Exception as registry_exc:  # noqa: BLE001
                failures.append(
                    f"capture registry failure: {type(registry_exc).__name__}: {registry_exc}"
                )
        health["failures"] = failures
        _record_health(health)
        print(f"Shadow run failed: {exc}", file=sys.stderr)
        return 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated WNBA PTS projection shadow model.")
    parser.add_argument("--screen-date", help="Slate date in YYYY-MM-DD format; defaults to today.")
    parser.add_argument("--simulations", type=int, default=10_000)
    parser.add_argument(
        "--calibration-artifact",
        default="config/shadow_v2_calibration.json",
        help=(
            "Path to the frozen residual calibration artifact (absolute, or "
            "relative to the project root). The run fails fast when it is "
            "missing or invalid."
        ),
    )
    parser.add_argument("--capture-min-lead-minutes", type=float, default=20.0)
    parser.add_argument("--capture-max-lead-minutes", type=float, default=90.0)
    parser.add_argument(
        "--force-recapture",
        action="store_true",
        help="Research only: capture an already-recorded game again while it is in the window.",
    )
    parser.add_argument(
        "--include-started",
        action="store_true",
        help="Research only: include games that have already started.",
    )
    return parser.parse_args()


def _eligible_games(
    games: list[Game],
    *,
    include_started: bool,
    capture_time: datetime,
    capture_window: CaptureWindow,
    registry: CaptureRegistry,
    line_source: str,
    force_recapture: bool,
) -> list[Game]:
    if include_started:
        return games
    candidates = select_games_in_capture_window(
        games,
        now=capture_time,
        window=capture_window,
    )
    if force_recapture:
        return candidates
    return [
        game
        for game in candidates
        if not registry.is_captured(
            game_id=game.game_id,
            model_version=MODEL_VERSION,
            line_source=line_source,
        )
    ]


def _game_outcome(statuses: list[str], transient: bool) -> str:
    if not statuses:
        return "no_lines"
    any_projected = LINE_PROJECTED in statuses
    if transient:
        return "partial" if any_projected else "failed"
    return "complete"


def _rollup_outcome(outcomes: dict[str, str]) -> str:
    values = list(outcomes.values())
    if not values:
        return "no_lines"
    if all(value == "complete" for value in values):
        return "captured"
    if all(value == "no_lines" for value in values):
        return "no_lines"
    if any(value in {"partial", "failed"} for value in values):
        return "partial" if any(value in {"complete", "partial"} for value in values) else "failed"
    return "captured"


def _collected_too_late(game: Game, prop_lines: list[PropLine], minimum_lead_minutes: float) -> bool:
    collected_at = max(
        (line.collected_at for line in prop_lines if line.event_id == game.game_id),
        default=None,
    )
    if collected_at is None:
        return False
    return capture_lead_minutes(game.game_time, collected_at) < minimum_lead_minutes


def _git_state() -> tuple[str | None, bool]:
    commit = None
    dirty = False
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = commit_result.stdout.strip() or None
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        dirty = bool(status_result.stdout.strip())
    except Exception:  # noqa: BLE001
        return None, False
    return commit, dirty


def _load_injuries(cache: JsonCache, games: list[Game], screen_date: date) -> tuple[dict[str, list], dict[str, str]]:
    source = EspnInjurySource(cache)
    teams = sorted({game.home_team for game in games} | {game.away_team for game in games})
    results = {}
    errors = {}
    for team in teams:
        try:
            injuries = source.fetch_team_injuries(team, screen_date)
        except Exception as exc:  # noqa: BLE001
            errors[team] = f"{type(exc).__name__}: {exc}"
            continue
        if injuries:
            results[team] = injuries
    return results, errors


def _resolve_game_context(
    contexts: dict[tuple[str, str], ShadowGameContext],
    team: str,
    opponent: str,
) -> ShadowGameContext | None:
    return contexts.get((team, opponent)) or contexts.get((opponent, team))


def _team_spread(team: str, context: ShadowGameContext | None) -> float | None:
    if context is None:
        return None
    if context.home_team == team:
        return context.home_spread
    if context.away_team == team:
        return context.away_spread
    return None


def _export_shadow_snapshot(
    *,
    screen_date: date,
    games: list[Game],
    prop_lines: list[PropLine],
    projections: list,
    game_contexts: dict[tuple[str, str], ShadowGameContext],
    failures: list[str],
    config: ProjectionConfig,
    line_source: str,
    line_source_diagnostics: dict,
    capture_window: CaptureWindow,
    strict_pregame: bool,
    code_commit: str | None,
    code_dirty: bool,
    injury_errors: dict[str, str],
    residuals: ResidualArtifact,
) -> Path:
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(timezone.utc)
    timestamp = exported_at.strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"shadow_projection_{timestamp}.json"
    game_by_id = {game.game_id: game for game in games}
    serialized_projections = []
    for projection in projections:
        serialized = asdict(projection)
        game = game_by_id[projection.game_id]
        serialized["capture_lead_minutes"] = capture_lead_minutes(
            game.game_time,
            projection.line_collected_at,
        )
        serialized["captured_before_scheduled_start"] = (
            serialized["capture_lead_minutes"] > 0.0
        )
        serialized["projection_id"] = projection_id(serialized)
        serialized_projections.append(serialized)
    payload = {
        "mode": "shadow_projection",
        "research_only": True,
        "model_version": MODEL_VERSION,
        "model_config": asdict(config),
        "model_config_hash": config_signature(config),
        "residual_model": {
            "residual_model_id": residuals.residual_model_id,
            "schema_version": residuals.schema_version,
            "n_rows": residuals.n_rows,
            "sha256": residuals.sha256,
            "source_model_version": residuals.source_model_version,
            "source_config_hash": residuals.source_config_hash,
            "source_commit": residuals.source_commit,
            "projection_ids_sha256": residuals.projection_ids_sha256,
        },
        "code_commit": code_commit,
        "code_dirty": code_dirty,
        "snapshot_id": f"shadow-{timestamp}",
        "exported_at": exported_at.isoformat(),
        "screen_date": screen_date.isoformat(),
        "line_source": line_source,
        "line_source_diagnostics": line_source_diagnostics,
        "simulations": config.simulations,
        "capture_policy": {
            "strict_pregame": strict_pregame,
            "minimum_lead_minutes": capture_window.minimum_lead_minutes,
            "maximum_lead_minutes": capture_window.maximum_lead_minutes,
        },
        "games": [asdict(game) for game in games],
        "game_contexts": [
            {"key": list(key), "value": asdict(context)}
            for key, context in sorted(game_contexts.items())
        ],
        "prop_lines": [asdict(line) for line in prop_lines],
        "projections": serialized_projections,
        "failures": failures,
        "source_health": {
            "injury_errors": dict(sorted(injury_errors.items())),
        },
    }
    path.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True))
    return path


def _record_health(health: dict) -> None:
    log_dir = OUTPUTS_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    history_path = log_dir / "shadow_health.jsonl"
    latest_path = log_dir / "shadow_health_latest.json"
    with history_path.open("a") as handle:
        handle.write(json.dumps(health, sort_keys=True) + "\n")
    latest_path.write_text(json.dumps(health, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
