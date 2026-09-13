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
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wnba_props.board import build_daily_board, health_report, write_board, write_ledger
from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR, ESPN_TO_TEAM_ABBR, load_settings
from wnba_props.game_markets import (
    export_game_markets,
    fetch_game_markets,
    to_game_market,
)
from wnba_props.modeling.registry import ArtifactError, code_commit, load_artifacts
from wnba_props.notifiers.forecast_discord import (
    _has_rows,
    send_board,
    split_sections,
)
from wnba_props.sources.basketball_reference import BasketballReferenceSource
from wnba_props.sources.espn import EspnSlateSource
from wnba_props.sources.espn_gamelog import EspnGameLogSource
from wnba_props.sources.espn_injuries import EspnInjurySource
from wnba_props.sources.playerprops import PlayerPropsSource
from wnba_props.features.team import parse_team_results_from_scoreboard
from wnba_props.features.player import league_stat_baselines

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
        if not logs:
            try:
                logs = fallback.fetch_logs(line.player_name_raw, line.team, season)
            except Exception:  # noqa: BLE001
                logs = []
        if logs:
            logs_by_player[key] = logs

    player_statuses = {}
    for injuries in team_injuries.values():
        for injury in injuries:
            player_statuses[injury.player_name_norm] = injury.status
    return logs_by_player, player_statuses, team_injuries


def run_pipeline(
    *,
    slot: str,
    screen: str,
    send_discord: bool,
    force_send: bool = False,
    webhook_url: str | None = None,
    refresh: bool = True,
    simulations: int = 10_000,
) -> int:
    settings = load_settings()
    settings.screen_date = date.fromisoformat(screen)
    run_id = _run_id(screen, slot)

    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(CACHE_DIR / "lines", ttl_hours=settings.lines_cache_ttl_minutes / 60.0)
    injuries_cache = JsonCache(CACHE_DIR / "injuries", ttl_hours=settings.injuries_cache_ttl_minutes / 60.0)

    try:
        bundle = load_artifacts()
    except ArtifactError as exc:
        print(f"[pipeline] artifact gate failed: {exc}", file=sys.stderr)
        return 1

    print("[pipeline] stage=slate")
    games = EspnSlateSource().fetch_games(settings.screen_date)
    if settings.pregame_only:
        now = datetime.now(timezone.utc)
        games = [g for g in games if g.game_time > now]
    if not games:
        print(f"[pipeline] no eligible games for {screen}")
        return 0

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
        return 1

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
        return 1

    print("[pipeline] stage=player_logs")
    logs_by_player, player_statuses, team_injuries = _collect_player_logs(
        settings, games, prop_lines, shared_cache, injuries_cache
    )
    league_logs = [log for logs in logs_by_player.values() for log in logs]
    league_baselines = league_stat_baselines(
        league_logs, settings.supported_prop_types
    )

    print("[pipeline] stage=team_results")
    team_results = _load_team_results(settings.screen_date)

    print("[pipeline] stage=board")
    provenance = {
        "run_id": run_id,
        "slot": slot,
        "screen_date": screen,
        "code_commit": code_commit(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": bundle.versions(),
        "game_market_coverage": market_diags.get("coverage", {}),
        "line_source": settings.line_source,
        "bookmaker": settings.playerprops_book,
        "player_logs_loaded": len(logs_by_player),
        "injuries_by_team": {team: len(items) for team, items in team_injuries.items()},
        "league_baselines": {k: round(v, 3) for k, v in league_baselines.items()},
        "stale_prop_lines_dropped": dropped_stale,
        "game_markets_stale": bool(market_diags.get("bovada_stale")),
        "game_markets_bovada_error": market_diags.get("bovada_error"),
        "game_markets_polymarket_primary": bool(market_diags.get("polymarket_primary")),
    }
    board = build_daily_board(
        screen_date=screen,
        run_id=run_id,
        slot=slot,
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

    board_path = FORECAST_BOARDS_DIR / f"forecast_board_{screen}_{slot}.json"
    write_board(board_path, board)
    write_ledger(LEDGER_DIR / "forecast_ledger.jsonl", board)
    print(f"[pipeline] board -> {board_path}")
    print(
        f"[pipeline] rows={board.summary['total_rows']} priced={board.summary['priced_rows']} "
        f"favorable={board.summary['favorable_rows']} health={health['status']}"
    )

    if send_discord:
        if health["status"] != "ok":
            print(f"[pipeline] discord BLOCKED: {health['reasons']}", file=sys.stderr)
            return 0
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
            return 1
        else:
            error = results[0].error if results else "no chunks"
            print(f"[pipeline] discord failed: {error}", file=sys.stderr)
            return 1
    return 0


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
    if not (settings.discord_team_webhook_url or settings.discord_player_webhook_url):
        return send_board(
            primary,
            board.sections,
            screen_date=screen,
            slot=slot,
            slot_label=slot.title(),
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
                delivery_ledger=DISCORD_LEDGER,
                force_send=force_send,
            )
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the atomic WNBA forecast pipeline.")
    parser.add_argument("--slot", default="evening", choices=("morning", "afternoon", "evening"))
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to today.")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--force-send", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--no-refresh", action="store_true")
    parser.add_argument("--simulations", type=int, default=10_000)
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
