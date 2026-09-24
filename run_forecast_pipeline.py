"""Atomic WNBA forecast pipeline: collect fresh inputs, then publish the board.

Stages, in order: slate -> game markets (Bovada primary, Polymarket fallback)
-> player props -> point-in-time player logs/injuries -> versioned artifacts
-> price-independent forecasts -> board + ledgers -> optional Discord.

A stage that fails (missing slate, no markets, missing artifacts) stops the
pipeline, so the board can never publish from stale or partial inputs. The
whole pipeline runs under the shared task lock held by scripts/run_linux_task.sh.

Usage:
    python3 run_forecast_pipeline.py --slot evening --send-discord
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wnba_props.board import build_daily_board, health_report, write_board, write_ledger
from wnba_props.cache import JsonCache
from wnba_props.config import (
    CACHE_DIR,
    ESPN_TO_TEAM_ABBR,
    OUTPUTS_DIR,
    PLAYER_POSITIONS_PATH,
    load_settings,
)
from wnba_props.game_markets import (
    export_game_markets,
    fetch_game_markets,
    to_game_market,
)
from wnba_props.modeling.registry import ArtifactError, code_commit, load_artifacts
from wnba_props.notifiers.forecast_discord import (
    _has_rows,
    send_board,
    send_health_alert,
    split_sections,
)
from wnba_props.sources.basketball_reference import BasketballReferenceSource
from wnba_props.sources.espn import EspnSlateSource
from wnba_props.sources.espn_gamelog import EspnGameLogSource
from wnba_props.sources.espn_injuries import EspnInjurySource
from wnba_props.sources.playerprops import PlayerPropsSource
from wnba_props.features.team import parse_team_results_from_scoreboard
from wnba_props.features.player import league_stat_baselines, league_positional_baselines
from wnba_props.features.positions import (
    fetch_position_map,
    load_position_map,
    merge_position_maps,
)

FORECAST_BOARDS_DIR = OUTPUTS_DIR / "forecast_boards"
LEDGER_DIR = OUTPUTS_DIR / "ledger"
HISTORY_DIR = OUTPUTS_DIR / "history"
SCOREBOARD_CACHE_DIR = OUTPUTS_DIR / "research" / "scoreboard_cache"
DISCORD_LEDGER = LEDGER_DIR / "discord_delivery.jsonl"
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
)


def _run_id(screen_date: str, slot: str) -> str:
    return f"forecast-{screen_date}-{slot}"


def _latest_log_date(logs) -> date | None:
    if not logs:
        return None
    return max(log.game_date for log in logs)


def _player_logs_stale(screen_date: date, logs, *, phase: str) -> bool:
    """Playoff guard: a nonempty but stale regular-season log is not enough."""
    if phase != "playoff":
        return False
    latest = _latest_log_date(logs)
    if latest is None:
        return True
    return latest < screen_date - timedelta(days=2)


def _player_logs_newer(fallback_logs, logs) -> bool:
    fallback_latest = _latest_log_date(fallback_logs)
    current_latest = _latest_log_date(logs)
    if fallback_latest is None:
        return False
    return current_latest is None or fallback_latest > current_latest


def _config_fingerprint(settings) -> str:
    payload = {
        "supported_prop_types": sorted(settings.supported_prop_types),
        "playerprops_book": settings.playerprops_book,
        "market_blend_weight": settings.market_blend_weight,
        "ev_side_selection": settings.ev_side_selection,
        "season_phase": settings.season_phase,
        "min_event_match_ratio": settings.min_event_match_ratio,
        "min_player_load_ratio": settings.min_player_load_ratio,
        "max_line_age_minutes": settings.max_line_age_minutes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


# Stop retrying a pregame capture once the earliest tip is this close; a late
# capture is worse than letting the evening run cover the game.
PREGAME_TIP_BUFFER_MINUTES = 15


def _board_path(screen: str, slot: str, snapshot_id: str) -> Path:
    """Immutable board filename: every capture gets its own file.

    The ledger (not the filename) decides which capture is official, so two
    captures of the same date/slot can never overwrite each other.
    """
    return FORECAST_BOARDS_DIR / f"forecast_board_{screen}_{slot}_{snapshot_id}.json"


def _pregame_capture_target(
    screen_date: date,
    *,
    lead_minutes: int,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Return (capture_at, earliest_tip) for a schedule-aware pregame run.

    The capture is scheduled ``lead_minutes`` before the earliest game that has
    not yet tipped. Returns ``(None, None)`` when no future games remain.
    """
    current = now or datetime.now(timezone.utc)
    games = EspnSlateSource().fetch_games(screen_date)
    future = [game for game in games if game.game_time > current]
    if not future:
        return None, None
    earliest_tip = min(game.game_time for game in future)
    return earliest_tip - timedelta(minutes=lead_minutes), earliest_tip


def _sleep_until(target: datetime) -> None:
    """Sleep until ``target`` (no-op when the target already passed)."""
    while True:
        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        print(f"[pipeline] pregame wait: {remaining / 60:.1f}m until capture window")
        time.sleep(min(remaining, 300.0))


def _load_team_results(screen_date: date) -> list:
    """Point-in-time team results from ESPN scoreboards (cached per date)."""
    from wnba_props.utils import fetch_espn_json

    SCOREBOARD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    current = date(screen_date.year, 5, 1)
    while current < screen_date:
        cache_file = SCOREBOARD_CACHE_DIR / f"scoreboard_{current.strftime('%Y%m%d')}.json"
        if cache_file.exists():
            try:
                payload = json.loads(cache_file.read_text())
            except json.JSONDecodeError:
                payload = None
        else:
            payload = None
        if payload is None:
            try:
                payload = fetch_espn_json(SCOREBOARD_URL.format(date_str=current.strftime("%Y%m%d")))
            except Exception:  # noqa: BLE001 - a missing day just reduces history
                current += timedelta(days=1)
                continue
            cache_file.write_text(json.dumps(payload))
        results.extend(
            parse_team_results_from_scoreboard(
                payload, game_date=current, team_map=ESPN_TO_TEAM_ABBR
            )
        )
        current += timedelta(days=1)
    return results


def _collect_player_logs(settings, games, prop_lines, shared_cache, injuries_cache):
    """Load point-in-time player logs for every prop subject.

    Uses Basketball-Reference as primary with an ESPN gamelog fallback. No
    legacy staleness discard: the forecast model trains on season-long history,
    and a schedule break must not empty the feature table.
    """
    logs_source = BasketballReferenceSource(
        shared_cache, sticky_daily_cache=settings.sticky_daily_log_cache
    )
    fallback = EspnGameLogSource(shared_cache)
    season = settings.screen_date.year

    injury_source = EspnInjurySource(injuries_cache)
    team_injuries = {}
    for team in sorted({g.home_team for g in games} | {g.away_team for g in games}):
        try:
            injuries = injury_source.fetch_team_injuries(team, settings.screen_date)
        except Exception:  # noqa: BLE001 - injuries never block the board
            injuries = []
        if injuries:
            team_injuries[team] = injuries

    logs_by_player = {}
    seen = set()
    for line in prop_lines:
        key = line.player_name_norm
        if key in seen:
            continue
        seen.add(key)
        logs = []
        try:
            logs = logs_source.fetch_logs(line.player_name_raw, line.team, season)
        except Exception:  # noqa: BLE001
            logs = []
        if not logs or _player_logs_stale(
            settings.screen_date, logs, phase=settings.season_phase
        ):
            try:
                fallback_logs = fallback.fetch_logs(
                    line.player_name_raw, line.team, season
                )
            except Exception:  # noqa: BLE001
                fallback_logs = []
            if fallback_logs and _player_logs_newer(fallback_logs, logs):
                logs = fallback_logs
        if logs:
            logs_by_player[key] = logs

    player_statuses = {}
    for injuries in team_injuries.values():
        for injury in injuries:
            player_statuses[injury.player_name_norm] = injury.status
    return logs_by_player, player_statuses, team_injuries


def _run_pipeline_once(
    *,
    slot: str,
    screen: str,
    send_discord: bool,
    force_send: bool = False,
    webhook_url: str | None = None,
    refresh: bool = True,
    simulations: int = 10_000,
) -> tuple[int, bool]:
    """Run one board capture; returns (exit_code, healthy)."""
    settings = load_settings()
    settings.screen_date = date.fromisoformat(screen)
    run_id = _run_id(screen, slot)
    snapshot_id = f"{run_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(CACHE_DIR / "lines", ttl_hours=settings.lines_cache_ttl_minutes / 60.0)
    injuries_cache = JsonCache(CACHE_DIR / "injuries", ttl_hours=settings.injuries_cache_ttl_minutes / 60.0)

    try:
        bundle = load_artifacts()
    except ArtifactError as exc:
        print(f"[pipeline] artifact gate failed: {exc}", file=sys.stderr)
        return 1, False

    print("[pipeline] stage=slate")
    games = EspnSlateSource().fetch_games(settings.screen_date)
    if settings.pregame_only:
        now = datetime.now(timezone.utc)
        games = [g for g in games if g.game_time > now]
    if not games:
        print(f"[pipeline] no eligible games for {screen}")
        return 0, True

    print("[pipeline] stage=game_markets")
    snapshots, market_diags = fetch_game_markets(
        screen_date=settings.screen_date,
        cache_dir=CACHE_DIR / "lines",
        refresh=refresh,
        max_cache_age_seconds=settings.max_line_age_minutes * 60.0,
    )
    export_game_markets(
        HISTORY_DIR / f"game_markets_{screen}.json",
        screen_date=settings.screen_date,
        snapshots=snapshots,
        diagnostics=market_diags,
    )
    if not snapshots:
        print("[pipeline] no game markets collected; continuing with props only", file=sys.stderr)
    game_markets = {key: to_game_market(snap) for key, snap in snapshots.items()}

    print("[pipeline] stage=player_props")
    line_source = PlayerPropsSource(settings, lines_cache)
    prop_lines = line_source.fetch_prop_lines(games)
    if not prop_lines:
        print("[pipeline] no player prop lines found", file=sys.stderr)
        return 1, False

    max_age = timedelta(minutes=settings.max_line_age_minutes)
    now_utc = datetime.now(timezone.utc)
    fresh_lines = [line for line in prop_lines if (now_utc - line.collected_at) <= max_age]
    dropped_stale = len(prop_lines) - len(fresh_lines)
    if dropped_stale:
        print(
            f"[pipeline] dropped {dropped_stale} stale prop line(s) older than "
            f"{settings.max_line_age_minutes}m",
            file=sys.stderr,
        )
    prop_lines = fresh_lines
    if not prop_lines:
        print("[pipeline] all player prop lines were stale", file=sys.stderr)
        return 1, False

    print("[pipeline] stage=player_logs")
    logs_by_player, player_statuses, team_injuries = _collect_player_logs(
        settings, games, prop_lines, shared_cache, injuries_cache
    )
    league_logs = [log for logs in logs_by_player.values() for log in logs]
    league_baselines = league_stat_baselines(
        league_logs, settings.supported_prop_types
    )

    print("[pipeline] stage=positions")
    position_map = merge_position_maps(
        load_position_map(PLAYER_POSITIONS_PATH),
        fetch_position_map({log.team for log in league_logs}, shared_cache),
    )
    positional_baselines = league_positional_baselines(
        league_logs, settings.supported_prop_types, position_map
    )

    print("[pipeline] stage=team_results")
    team_results = _load_team_results(settings.screen_date)

    print("[pipeline] stage=board")
    provenance = {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "phase": settings.season_phase,
        "market_blend_weight": settings.market_blend_weight,
        "ev_side_selection": settings.ev_side_selection,
        "config_hash": _config_fingerprint(settings),
        "slot": slot,
        "screen_date": screen,
        "code_commit": code_commit(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": bundle.versions(),
        "game_market_coverage": market_diags.get("coverage", {}),
        "line_source": settings.line_source,
        "line_source_snapshot": getattr(line_source, "raw_snapshot_path", ""),
        "line_source_snapshot_sha256": getattr(
            line_source, "raw_snapshot_sha256", ""
        ),
        "bookmaker": settings.playerprops_book,
        "player_logs_loaded": len(logs_by_player),
        "injuries_by_team": {team: len(items) for team, items in team_injuries.items()},
        "league_baselines": {k: round(v, 3) for k, v in league_baselines.items()},
        "positions_loaded": len(position_map),
        "positional_baselines": {
            f"{prop}:{pos}": round(value, 3)
            for (prop, pos), value in positional_baselines.items()
        },
        "stale_prop_lines_dropped": dropped_stale,
        "game_markets_stale": bool(market_diags.get("bovada_stale")),
        "game_markets_bovada_error": market_diags.get("bovada_error"),
        "game_markets_polymarket_primary": bool(market_diags.get("polymarket_primary")),
    }
    board = build_daily_board(
        screen_date=screen,
        run_id=run_id,
        slot=slot,
        snapshot_id=snapshot_id,
        phase=settings.season_phase,
        market_weight=settings.market_blend_weight,
        ev_selection=settings.ev_side_selection,
        slate=games,
        prop_lines=prop_lines,
        league_logs=league_logs,
        team_results=team_results,
        game_engine=bundle.game,
        residual_artifacts=bundle.residuals,
        game_markets=game_markets,
        market_provenance=snapshots,
        player_statuses=player_statuses,
        league_baselines=league_baselines,
        player_positions=position_map,
        positional_baselines=positional_baselines,
        provenance=provenance,
        simulations=simulations,
    )

    coverage = market_diags.get("coverage", {}) if isinstance(market_diags, dict) else {}
    health = health_report(
        board,
        require_priced=True,
        slate_games=len(games),
        evaluated_lines=len(prop_lines),
        min_evaluated_lines=settings.min_evaluated_lines,
        min_event_match_ratio=settings.min_event_match_ratio,
        min_player_load_ratio=settings.min_player_load_ratio,
        games_with_markets=int(coverage.get("snapshots", 0)),
    )
    board.summary["health"] = health["status"]
    board.summary["health_reasons"] = health["reasons"]

    board_path = _board_path(screen, slot, snapshot_id)
    write_board(board_path, board)
    write_ledger(LEDGER_DIR / "forecast_ledger.jsonl", board)
    print(f"[pipeline] board -> {board_path}")
    print(
        f"[pipeline] rows={board.summary['total_rows']} priced={board.summary['priced_rows']} "
        f"favorable={board.summary['favorable_rows']} health={health['status']}"
    )

    if send_discord:
        webhook = webhook_url or settings.discord_webhook_url
        if health["status"] != "ok":
            print(f"[pipeline] discord BLOCKED: {health['reasons']}", file=sys.stderr)
            if webhook:
                alert = send_health_alert(
                    webhook,
                    screen_date=screen,
                    slot=slot,
                    health=health,
                )
                if alert.ok:
                    print("[pipeline] discord health alert sent")
                else:
                    print("[pipeline] discord health alert failed", file=sys.stderr)
            return 0, False
        results = _send_discord(
            board,
            settings=settings,
            screen=screen,
            slot=slot,
            webhook_url=webhook_url,
            force_send=force_send,
        )
        if results and all(result.ok for result in results):
            print(f"[pipeline] discord sent ({len(results)} message(s))")
        elif any(result.ok for result in results):
            print("[pipeline] discord partially sent", file=sys.stderr)
            return 1, False
        else:
            error = results[0].error if results else "no chunks"
            print(f"[pipeline] discord failed: {error}", file=sys.stderr)
            return 1, False
    return 0, True


def run_pipeline(
    *,
    slot: str,
    screen: str,
    send_discord: bool,
    force_send: bool = False,
    webhook_url: str | None = None,
    refresh: bool = True,
    simulations: int = 10_000,
    pregame_lead_minutes: int = 75,
    pregame_retry_minutes: int = 15,
    pregame_max_retries: int = 3,
    pregame_wait: bool = True,
) -> int:
    """Run the forecast pipeline, with schedule-aware pregame behavior.

    For the ``pregame`` slot this waits until ``pregame_lead_minutes`` before
    the earliest untipped game, runs one capture, and retries an unhealthy
    board until the attempts are exhausted or the earliest tip is within
    ``PREGAME_TIP_BUFFER_MINUTES``. Retries reuse the same ``pregame`` slot, so
    Discord duplicate suppression keeps them idempotent. All other slots run
    exactly once, as before.
    """
    if slot != "pregame" or not pregame_wait:
        code, _ = _run_pipeline_once(
            slot=slot,
            screen=screen,
            send_discord=send_discord,
            force_send=force_send,
            webhook_url=webhook_url,
            refresh=refresh,
            simulations=simulations,
        )
        return code

    screen_date = date.fromisoformat(screen)
    capture_at, earliest_tip = _pregame_capture_target(
        screen_date, lead_minutes=pregame_lead_minutes
    )
    if earliest_tip is None:
        print(f"[pipeline] pregame: no upcoming games for {screen}")
        return 0
    assert capture_at is not None
    print(
        f"[pipeline] pregame: earliest tip {earliest_tip.isoformat()}, "
        f"capture window opens {capture_at.isoformat()}"
    )
    _sleep_until(capture_at)

    attempts = 1 + max(0, pregame_max_retries)
    last_code = 0
    for attempt in range(1, attempts + 1):
        print(f"[pipeline] pregame: capture attempt {attempt}/{attempts}")
        last_code, healthy = _run_pipeline_once(
            slot=slot,
            screen=screen,
            send_discord=send_discord,
            force_send=force_send,
            webhook_url=webhook_url,
            refresh=refresh,
            simulations=simulations,
        )
        if last_code == 0 and healthy:
            return 0
        if attempt >= attempts:
            break
        now = datetime.now(timezone.utc)
        cutoff = earliest_tip - timedelta(minutes=PREGAME_TIP_BUFFER_MINUTES)
        if now >= cutoff:
            print("[pipeline] pregame: too close to tip; leaving evening run to cover")
            break
        wait_seconds = min(
            pregame_retry_minutes * 60.0, (cutoff - now).total_seconds()
        )
        print(f"[pipeline] pregame: retrying in {wait_seconds / 60:.1f}m")
        time.sleep(max(0.0, wait_seconds))
    return last_code


def _send_discord(
    board,
    *,
    settings,
    screen: str,
    slot: str,
    webhook_url: str | None,
    force_send: bool,
) -> list:
    """Send the board to one channel, or split team/player across two."""
    team_sections, player_sections = split_sections(board.sections)
    primary = webhook_url or settings.discord_webhook_url
    team_hook = settings.discord_team_webhook_url or primary
    player_hook = settings.discord_player_webhook_url or primary

    provenance = board.provenance or {}
    source_note = ""
    if provenance.get("game_markets_polymarket_primary"):
        source_note = "Game prices: Polymarket reference"
    elif provenance.get("game_markets_stale"):
        source_note = "Warning: stale game prices (fallback cache)"

    if not (settings.discord_team_webhook_url or settings.discord_player_webhook_url):
        return send_board(
            primary,
            board.sections,
            screen_date=screen,
            slot=slot,
            slot_label=slot.title(),
            source_note=source_note,
            delivery_ledger=DISCORD_LEDGER,
            force_send=force_send,
        )

    results: list = []
    if _has_rows(team_sections):
        results.extend(
            send_board(
                team_hook,
                team_sections,
                screen_date=screen,
                slot=f"{slot}:team",
                slot_label=slot.title(),
                title="WNBA Team Board",
                source_note=source_note,
                delivery_ledger=DISCORD_LEDGER,
                force_send=force_send,
            )
        )
    if _has_rows(player_sections):
        results.extend(
            send_board(
                player_hook,
                player_sections,
                screen_date=screen,
                slot=f"{slot}:props",
                slot_label=slot.title(),
                title="WNBA Player Props",
                source_note=source_note,
                delivery_ledger=DISCORD_LEDGER,
                force_send=force_send,
            )
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the atomic WNBA forecast pipeline.")
    parser.add_argument(
        "--slot",
        default="evening",
        choices=("morning", "afternoon", "pregame", "evening"),
    )
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to today.")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--force-send", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--no-refresh", action="store_true")
    parser.add_argument("--simulations", type=int, default=10_000)
    parser.add_argument(
        "--pregame-lead-minutes",
        type=int,
        default=75,
        help="Pregame slot: capture this many minutes before the earliest tip.",
    )
    parser.add_argument(
        "--pregame-retry-minutes",
        type=int,
        default=15,
        help="Pregame slot: wait between retries of an unhealthy board.",
    )
    parser.add_argument(
        "--pregame-max-retries",
        type=int,
        default=3,
        help="Pregame slot: retries after the first capture attempt.",
    )
    parser.add_argument(
        "--no-pregame-wait",
        action="store_true",
        help="Pregame slot: capture immediately instead of waiting for the window.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    screen = args.date or date.today().isoformat()
    return run_pipeline(
        slot=args.slot,
        screen=screen,
        send_discord=args.send_discord,
        force_send=args.force_send,
        webhook_url=args.webhook_url,
        refresh=not args.no_refresh,
        simulations=args.simulations,
        pregame_lead_minutes=args.pregame_lead_minutes,
        pregame_retry_minutes=args.pregame_retry_minutes,
        pregame_max_retries=args.pregame_max_retries,
        pregame_wait=not args.no_pregame_wait,
    )


if __name__ == "__main__":
    raise SystemExit(main())
