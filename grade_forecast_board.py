"""Grade a published WNBA forecast board and update the ROI ledger.

Resolves game moneylines, spreads and totals from the ESPN scoreboard, player
props from ESPN boxscores, settles flat-stake units at the captured price, and
optionally posts a Discord recap.

Usage:
    python3 grade_forecast_board.py [--date YYYY-MM-DD] [--send-discord]
"""
from __future__ import annotations

import argparse
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
    load_rows,
    roi_summary,
    settle_rows,
)
from wnba_props.notifiers.forecast_discord import send_recap
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
    graded = [row for row in rows if row.get("outcome") in (WIN, LOSS, PUSH, VOID)]
    overall = roi_summary(rows)
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
    pending = sum(1 for row in rows if row.get("outcome") in (PENDING, UNPRICED))
    return {
        "overall": overall,
        "by_market": by_market,
        "pending": pending,
    }


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

    rows = [row for row in load_rows(FORECAST_LEDGER) if row.get("game_date") == screen_date.isoformat()]
    if not rows:
        print(f"[grade] no ledger rows for {screen_date.isoformat()}")
        return 0

    scores = _final_scores(screen_date)
    player_stats = _player_stats(screen_date)

    results: dict[str, tuple[str, float | None]] = {}
    for row in rows:
        if row.get("outcome") != PENDING:
            continue
        proposition_id = str(row.get("proposition_id", ""))
        outcome, units = grade_ledger_row(row, scores, player_stats)
        if outcome == PENDING:
            continue
        results[proposition_id] = (outcome, units)

    updated = settle_rows(FORECAST_LEDGER, results)
    graded_rows = [
        row
        for row in load_rows(FORECAST_LEDGER)
        if row.get("game_date") == screen_date.isoformat()
    ]
    summary = _summarize(graded_rows)

    GRADES_DIR.mkdir(parents=True, exist_ok=True)
    artifact = GRADES_DIR / f"forecast_grade_{screen_date.isoformat()}.json"
    artifact.write_text(
        json.dumps(
            {
                "screen_date": screen_date.isoformat(),
                "graded_at": datetime.now(timezone.utc).isoformat(),
                "updated_rows": updated,
                "games_loaded": len(scores),
                "players_loaded": len(player_stats),
                "summary": summary,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(
        f"[grade] updated={updated} overall={summary['overall']['wins']}-"
        f"{summary['overall']['losses']}-{summary['overall']['pushes']} "
        f"units={summary['overall']['units']:+.2f}"
    )

    if send_discord:
        webhook = webhook_url or settings.discord_webhook_url
        result = send_recap(webhook, screen_date=screen_date.isoformat(), summary=summary)
        if not result.ok:
            print(f"[grade] discord recap failed: {result.error}", file=sys.stderr)
            return 1
        print("[grade] discord recap sent")
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
