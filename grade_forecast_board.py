"""Grade a published WNBA forecast board and update the ROI ledger.

Resolves game moneylines, spreads and totals from the ESPN scoreboard, player
props from ESPN boxscores, settles flat-stake units at the captured price, and
optionally posts a Discord recap.

Usage:
    python3 grade_forecast_board.py [--date YYYY-MM-DD] [--send-discord]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR, ESPN_TO_TEAM_ABBR, load_settings
from wnba_props.features.team import parse_team_results_from_scoreboard
from wnba_props.grading import grade_over_under, settle_units
from wnba_props.ledger import (
    LOSS,
    PENDING,
    PUSH,
    UNPRICED,
    VOID,
    WIN,
    is_recommended,
    latest_rows,
    load_rows,
    recommended_rows,
    roi_summary,
    settle_rows,
)
from wnba_props.notifiers.forecast_discord import (
    PLAYER_MARKETS,
    TEAM_MARKETS,
    send_recap,
)
from wnba_props.shadow.sources import ShadowEspnBoxscoreSource, ShadowEspnSlateSource
from wnba_props.utils import fetch_espn_json, normalize_name

LEDGER_DIR = OUTPUTS_DIR / "ledger"
FORECAST_LEDGER = LEDGER_DIR / "forecast_ledger.jsonl"
GRADES_DIR = OUTPUTS_DIR / "grades"
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
)


def _final_scores(screen_date: date) -> dict[tuple[str, str], tuple[int, int]]:
    payload = fetch_espn_json(SCOREBOARD_URL.format(date_str=screen_date.strftime("%Y%m%d")))
    scores: dict[tuple[str, str], tuple[int, int]] = {}
    for result in parse_team_results_from_scoreboard(
        payload, game_date=screen_date, team_map=ESPN_TO_TEAM_ABBR
    ):
        if result.home:
            scores[(result.team, result.opponent)] = (
                result.team_score,
                result.opponent_score,
            )
    return scores


def _player_stats(screen_date: date) -> dict[str, object]:
    cache = JsonCache(CACHE_DIR / "shared", ttl_hours=24)
    slate_source = ShadowEspnSlateSource()
    games = slate_source.fetch_games(screen_date)
    boxscore_source = ShadowEspnBoxscoreSource(cache)
    stats: dict[str, object] = {}
    for game in games:
        try:
            lines = boxscore_source.fetch_boxscore(game.game_id)
        except Exception:  # noqa: BLE001 - missing boxscore leaves props pending
            continue
        for (player_norm, _team), line in lines.items():
            stats.setdefault(player_norm, line)
    return stats


_STAT_FIELDS = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "3PM": "threes_made",
}


def grade_ledger_row(row: dict, scores: dict, player_stats: dict) -> tuple[str, float | None]:
    market = str(row.get("market", ""))
    pick = str(row.get("pick", ""))
    line = row.get("line")
    price = row.get("price")
    subject = str(row.get("subject", ""))

    if market in ("ML", "SPREAD", "TOTAL"):
        if " @ " not in subject:
            return PENDING, None
        away, home = subject.split(" @ ", 1)
        game_scores = scores.get((home, away))
        if game_scores is None:
            return PENDING, None
        home_score, away_score = game_scores
        if market == "ML":
            home_won = home_score > away_score
            won = home_won if pick == "HOME" else not home_won
            outcome = WIN if won else LOSS
        elif market == "SPREAD":
            if line is None:
                return PENDING, None
            adjusted = (home_score - away_score) + float(line)
            if abs(adjusted) < 1e-9:
                outcome = PUSH
            elif pick == "HOME":
                outcome = WIN if adjusted > 0 else LOSS
            else:
                outcome = WIN if adjusted < 0 else LOSS
        else:  # TOTAL
            if line is None:
                return PENDING, None
            outcome = grade_over_under(pick, home_score + away_score, float(line))
        return outcome, settle_units(outcome, price)

    if market in _STAT_FIELDS:
        stat_line = player_stats.get(normalize_name(subject))
        if stat_line is None:
            return PENDING, None
        value = getattr(stat_line, _STAT_FIELDS[market], None)
        if value is None or getattr(stat_line, "minutes", 0.0) == 0.0:
            return VOID, 0.0
        if line is None:
            return PENDING, None
        outcome = grade_over_under(pick, float(value), float(line))
        return outcome, settle_units(outcome, price)

    return PENDING, None


def _summarize(rows: list[dict]) -> dict:
    """Summarize a date or subset with the recommended record as headline.

    ``overall`` covers only rows the board recommended (see
    :func:`wnba_props.ledger.is_recommended`); ``by_market`` uses the same rows.
    ``all_rows`` keeps the pooled figure over every latest row because the
    grader's earlier headline reported it, but that pool mixes plays the board
    told subscribers to skip with plays it published, so it is labelled context
    and is never the number quoted as the product's record.
    """
    graded = [
        row
        for row in recommended_rows(rows)
        if row.get("outcome") in (WIN, LOSS, PUSH, VOID)
        and row.get("units") is not None
    ]
    overall = roi_summary(rows, recommended_only=True)
    overall["basis"] = "recommended plays only (paper_play when present, else value == playable)"
    all_rows = roi_summary(rows)
    all_rows["basis"] = (
        "context only: every latest row, including rows the board did not recommend"
    )
    excluded_rows = [
        row
        for row in latest_rows(rows)
        if row.get("units") is not None
        and row.get("outcome") in {WIN, LOSS, PUSH}
        and not is_recommended(row)
    ]
    excluded = roi_summary(excluded_rows)
    excluded["basis"] = "settled latest rows the board did not recommend"
    by_market: dict[str, dict] = {}
    for row in graded:
        market = str(row.get("market", ""))
        bucket = by_market.setdefault(market, {"wins": 0, "losses": 0, "pushes": 0, "units": 0.0, "plays": 0})
        outcome = row.get("outcome")
        if outcome == WIN:
            bucket["wins"] += 1
        elif outcome == LOSS:
            bucket["losses"] += 1
        elif outcome == PUSH:
            bucket["pushes"] += 1
        if outcome in (WIN, LOSS, PUSH) and row.get("units") is not None:
            bucket["units"] = round(bucket["units"] + float(row["units"]), 3)
            bucket["plays"] += 1
    for market, bucket in by_market.items():
        bucket["roi"] = round(bucket["units"] / bucket["plays"], 4) if bucket["plays"] else None
    pending = sum(
        1
        for row in rows
        if row.get("outcome") == PENDING
        or (row.get("outcome") == UNPRICED and not row.get("graded"))
    )
    unpriced = [
        row
        for row in rows
        if row.get("outcome") in (WIN, LOSS, PUSH, VOID)
        and row.get("units") is None
        and row.get("graded")
    ]
    paper_rows = [row for row in rows if row.get("paper_play")]
    return {
        "overall": overall,
        "all_rows": all_rows,
        "excluded": excluded,
        "by_market": by_market,
        "pending": pending,
        "unpriced": {
            "evaluated": len(unpriced),
            "wins": sum(1 for row in unpriced if row.get("outcome") == WIN),
            "losses": sum(1 for row in unpriced if row.get("outcome") == LOSS),
            "pushes": sum(1 for row in unpriced if row.get("outcome") == PUSH),
            "voids": sum(1 for row in unpriced if row.get("outcome") == VOID),
        },
    }


def _subset_summary(rows: list[dict], markets: tuple[str, ...]) -> dict:
    subset = [row for row in rows if str(row.get("market", "")) in markets]
    return _summarize(subset)


def _phase_for_row(row: dict, default: str) -> str:
    return str(row.get("phase") or default or "regular").lower()


def _phase_summary(rows: list[dict], default_phase: str) -> dict:
    """Cumulative summary by season phase, using only the latest capture.

    Each phase carries the combined record plus separate ``team`` (moneyline,
    spread, total) and ``player`` (PTS, REB, AST, 3PM) views. The two books
    share the pipeline but are read and compared differently, so the split is
    recorded alongside ``by_market`` rather than left to be re-derived.
    """
    by_phase: dict[str, list[dict]] = {}
    for row in latest_rows(rows):
        phase = _phase_for_row(row, default_phase)
        by_phase.setdefault(phase, []).append(row)
    summary: dict[str, dict] = {}
    for phase, items in sorted(by_phase.items()):
        phase_summary = _summarize(items)
        phase_summary["team"] = _subset_summary(items, TEAM_MARKETS)
        phase_summary["player"] = _subset_summary(items, PLAYER_MARKETS)
        summary[phase] = phase_summary
    return summary


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _phase_summary_provenance(ledger_path: Path, rows: list[dict]) -> dict:
    """Name the ledger this aggregate was derived from, and fingerprint it.

    ``outputs/grades/phase_summary.json`` is a cumulative *view*. Nothing else
    recomputes it, so without this block a stale copy is indistinguishable from
    an independent account of the same record.
    """
    try:
        digest = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    except OSError:
        digest = None
    return {
        "derived": True,
        "derived_from": _display_path(ledger_path),
        "derived_from_sha256": digest,
        "ledger_rows": len(rows),
        "selection": "latest capture per proposition_id",
        "row_basis": {
            "overall": "rows the board recommended (paper_play, else value == playable)",
            "all_rows": "every priced row; context only, never the record",
            "excluded": "settled priced rows that were not recommended",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Cumulative display aggregate, not a second source of truth. Quote ROI "
            "only from a fresh recompute of the ledger above."
        ),
    }


def _phase_summary_document(
    rows: list[dict], default_phase: str, ledger_path: Path
) -> dict:
    document = _phase_summary(rows, default_phase)
    document["_provenance"] = _phase_summary_provenance(ledger_path, rows)
    return document


def _recap_titles(phase: str) -> tuple[str, str, str]:
    if phase == "playoff":
        return (
            "WNBA Playoff Recap",
            "WNBA Team Playoff Recap",
            "WNBA Player Props Playoff Recap",
        )
    return "WNBA Forecast Recap", "WNBA Team Recap", "WNBA Player Props Recap"


def grade_date(
    screen_date: date,
    *,
    send_discord: bool = False,
    webhook_url: str | None = None,
) -> int:
    settings = load_settings()
    if not FORECAST_LEDGER.exists():
        print(f"[grade] no ledger at {FORECAST_LEDGER}", file=sys.stderr)
        return 1

    ledger_rows = load_rows(FORECAST_LEDGER)
    rows = [
        row
        for row in latest_rows(ledger_rows)
        if row.get("game_date") == screen_date.isoformat()
    ]
    if not rows:
        print(f"[grade] no ledger rows for {screen_date.isoformat()}")
        return 0

    scores = _final_scores(screen_date)
    player_stats = _player_stats(screen_date)

    results: dict[str, tuple[str, float | None]] = {}
    for row in rows:
        if row.get("outcome") not in (PENDING, UNPRICED):
            continue
        proposition_id = str(row.get("proposition_id", ""))
        outcome, units = grade_ledger_row(row, scores, player_stats)
        if outcome == PENDING:
            continue
        results[proposition_id] = (outcome, units)

    updated = settle_rows(FORECAST_LEDGER, results)
    ledger_rows = load_rows(FORECAST_LEDGER)
    graded_rows = [
        row
        for row in latest_rows(ledger_rows)
        if row.get("game_date") == screen_date.isoformat()
    ]
    summary = _summarize(graded_rows)
    phase = _phase_for_row(graded_rows[0], settings.season_phase) if graded_rows else settings.season_phase

    GRADES_DIR.mkdir(parents=True, exist_ok=True)
    artifact = GRADES_DIR / f"forecast_grade_{screen_date.isoformat()}.json"
    phase_summary = _phase_summary_document(
        ledger_rows, settings.season_phase, FORECAST_LEDGER
    )
    artifact.write_text(
        json.dumps(
            {
                "screen_date": screen_date.isoformat(),
                "phase": phase,
                "graded_at": datetime.now(timezone.utc).isoformat(),
                "updated_rows": updated,
                "games_loaded": len(scores),
                "players_loaded": len(player_stats),
                "summary": summary,
                "phase_summary": phase_summary,
            },
            indent=2,
            sort_keys=True,
        )
    )
    (GRADES_DIR / "phase_summary.json").write_text(
        json.dumps(phase_summary, indent=2, sort_keys=True)
    )
    recommended = summary["overall"]
    pooled = summary["all_rows"]
    print(
        f"[grade] updated={updated} recommended={recommended['wins']}-"
        f"{recommended['losses']}-{recommended['pushes']} "
        f"units={recommended['units']:+.2f} plays={recommended['plays']}"
    )
    print(
        f"[grade] context (not the record): all rows {pooled['wins']}-{pooled['losses']}-"
        f"{pooled['pushes']} units={pooled['units']:+.2f} plays={pooled['plays']} "
        f"({summary['excluded']['plays']} settled plays not recommended)"
    )

    if send_discord:
        primary = webhook_url or settings.discord_webhook_url
        team_hook = settings.discord_team_webhook_url or primary
        player_hook = settings.discord_player_webhook_url or primary
        split = bool(
            settings.discord_team_webhook_url or settings.discord_player_webhook_url
        )
        overall_title, team_title, player_title = _recap_titles(phase)
        if not split or team_hook == player_hook:
            result = send_recap(
                primary,
                screen_date=screen_date.isoformat(),
                summary=summary,
                title=overall_title,
            )
            if not result.ok:
                print(f"[grade] discord recap failed: {result.error}", file=sys.stderr)
                return 1
            print("[grade] discord recap sent")
        else:
            team_summary = _subset_summary(graded_rows, TEAM_MARKETS)
            player_summary = _subset_summary(graded_rows, PLAYER_MARKETS)
            team_result = send_recap(
                team_hook,
                screen_date=screen_date.isoformat(),
                summary=team_summary,
                title=team_title,
            )
            player_result = send_recap(
                player_hook,
                screen_date=screen_date.isoformat(),
                summary=player_summary,
                title=player_title,
            )
            if not team_result.ok or not player_result.ok:
                error = team_result.error if not team_result.ok else player_result.error
                print(f"[grade] discord recap failed: {error}", file=sys.stderr)
                return 1
            print("[grade] discord recap sent (team + player)")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade a WNBA forecast board.")
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to yesterday.")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.date:
        screen = date.fromisoformat(args.date)
    else:
        screen = date.today() - timedelta(days=1)
    return grade_date(screen, send_discord=args.send_discord, webhook_url=args.webhook_url)


if __name__ == "__main__":
    raise SystemExit(main())
