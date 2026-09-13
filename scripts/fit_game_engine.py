"""Fit the WNBA game engine (winner / margin / total).

Point-in-time team features are derived from completed games only. Evaluation
uses expanding monthly out-of-fold folds before a final artifact is fit on all
available games.

Usage:
    python3 scripts/fit_game_engine.py --start 2026-05-01 --end 2026-09-10
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.config import ESPN_TO_TEAM_ABBR  # noqa: E402
from wnba_props.features.team import (  # noqa: E402
    TeamFeatures,
    TeamGameResult,
    build_team_features,
    parse_team_results_from_scoreboard,
)
from wnba_props.modeling.game import (  # noqa: E402
    fit_logistic,
    fit_ridge,
    margin_vector,
    total_vector,
    winner_vector,
)
from wnba_props.modeling.game_forecast import GameEngine, save_game_engine  # noqa: E402
from wnba_props.modeling.registry import code_commit  # noqa: E402

SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
)

DEFAULT_RESULTS = REPO_ROOT / "outputs" / "research" / "team_results_2026.json"
DEFAULT_CACHE = REPO_ROOT / "outputs" / "research" / "scoreboard_cache"
DEFAULT_OUT = REPO_ROOT / "wnba_props" / "artifacts" / "game_engine_artifact.json"


def _iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def load_team_results(
    *,
    start: date,
    end: date,
    results_path: Path,
    cache_dir: Path,
    fetch: bool,
) -> list[TeamGameResult]:
    if results_path.exists():
        payload = json.loads(results_path.read_text())
        return [
            TeamGameResult(
                game_date=date.fromisoformat(item["game_date"]),
                team=item["team"],
                opponent=item["opponent"],
                team_score=int(item["team_score"]),
                opponent_score=int(item["opponent_score"]),
                home=bool(item["home"]),
            )
            for item in payload
        ]

    if not fetch:
        raise SystemExit(
            f"no cached team results at {results_path}; re-run with --fetch"
        )

    from wnba_props.utils import fetch_espn_json

    cache_dir.mkdir(parents=True, exist_ok=True)
    results: list[TeamGameResult] = []
    for current in _iter_dates(start, end):
        cache_file = cache_dir / f"scoreboard_{current.strftime('%Y%m%d')}.json"
        if cache_file.exists():
            payload = json.loads(cache_file.read_text())
        else:
            try:
                payload = fetch_espn_json(
                    SCOREBOARD_URL.format(date_str=current.strftime("%Y%m%d"))
                )
            except Exception as exc:  # noqa: BLE001
                print(f"skip {current}: {exc}")
                continue
            cache_file.write_text(json.dumps(payload))
        results.extend(
            parse_team_results_from_scoreboard(
                payload, game_date=current, team_map=ESPN_TO_TEAM_ABBR
            )
        )

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(
            [
                {
                    "game_date": r.game_date.isoformat(),
                    "team": r.team,
                    "opponent": r.opponent,
                    "team_score": r.team_score,
                    "opponent_score": r.opponent_score,
                    "home": r.home,
                }
                for r in results
            ],
            indent=2,
            sort_keys=True,
        )
    )
    return results


def _scoreboard_games(results: list[TeamGameResult]) -> list[tuple[TeamGameResult, TeamGameResult]]:
    """Return (home_result, away_result) pairs for completed games."""
    by_key = {(r.game_date, r.team): r for r in results}
    games: list[tuple[TeamGameResult, TeamGameResult]] = []
    seen: set[tuple[date, str, str]] = set()
    for result in results:
        if not result.home:
            continue
        away = by_key.get((result.game_date, result.opponent))
        if away is None:
            continue
        marker = (result.game_date, result.team, result.opponent)
        if marker in seen:
            continue
        seen.add(marker)
        games.append((result, away))
    games.sort(key=lambda pair: pair[0].game_date)
    return games


def build_training_rows(
    results: list[TeamGameResult],
) -> list[dict]:
    rows: list[dict] = []
    for home_result, away_result in _scoreboard_games(results):
        game_date = home_result.game_date
        home_features = build_team_features(
            team=home_result.team,
            opponent=home_result.opponent,
            game_date=game_date,
            results=results,
            is_home=True,
        )
        away_features = build_team_features(
            team=away_result.team,
            opponent=away_result.opponent,
            game_date=game_date,
            results=results,
            is_home=False,
        )
        if home_features is None or away_features is None:
            continue
        rows.append(
            {
                "game_date": game_date,
                "home": home_features,
                "away": away_features,
                "home_won": 1 if home_result.team_score > home_result.opponent_score else 0,
                "margin": float(home_result.team_score - home_result.opponent_score),
                "total": float(home_result.team_score + away_result.team_score),
            }
        )
    return rows


def fit_engine(rows: list[dict]) -> GameEngine:
    winner_rows = [winner_vector(r["home"], r["away"]) for r in rows]
    margin_rows = [margin_vector(r["home"], r["away"]) for r in rows]
    total_rows = [total_vector(r["home"], r["away"]) for r in rows]

    winner = fit_logistic(winner_rows, [r["home_won"] for r in rows], l2=1.0)
    margin = fit_ridge(margin_rows, [r["margin"] for r in rows], l2=4.0)
    total = fit_ridge(total_rows, [r["total"] for r in rows], l2=4.0)

    trained_through = max((r["game_date"] for r in rows), default=None)
    return GameEngine(
        winner=winner,
        margin=margin,
        total=total,
        trained_games=len(rows),
        feature_means={},
        trained_through=trained_through.isoformat() if trained_through else "",
        code_commit=code_commit(),
    )


def _brier(probabilities, outcomes) -> float:
    if not probabilities:
        return 0.0
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(probabilities)


def evaluate_oof(rows: list[dict]) -> list[dict]:
    months = sorted({(r["game_date"].year, r["game_date"].month) for r in rows})
    reports: list[dict] = []
    for year, month in months:
        month_start = date(year, month, 1)
        train = [r for r in rows if r["game_date"] < month_start]
        test = [
            r
            for r in rows
            if r["game_date"].year == year and r["game_date"].month == month
        ]
        if len(train) < 50 or len(test) == 0:
            continue
        engine = fit_engine(train)
        probs, outcomes, margin_errors, total_errors = [], [], [], []
        for r in test:
            p_home = engine.winner.predict_proba(winner_vector(r["home"], r["away"]))
            probs.append(p_home)
            outcomes.append(r["home_won"])
            margin_errors.append(
                abs(engine.margin.predict(margin_vector(r["home"], r["away"])) - r["margin"])
            )
            total_errors.append(
                abs(engine.total.predict(total_vector(r["home"], r["away"])) - r["total"])
            )
        accuracy = sum(
            1 for p, y in zip(probs, outcomes) if (p >= 0.5) == (y == 1)
        ) / len(test)
        reports.append(
            {
                "month": f"{year}-{month:02d}",
                "train_games": len(train),
                "test_games": len(test),
                "winner_accuracy": round(accuracy, 4),
                "winner_brier": round(_brier(probs, outcomes), 4),
                "margin_mae": round(mean(margin_errors), 4),
                "total_mae": round(mean(total_errors), 4),
            }
        )
    return reports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-05-01")
    parser.add_argument("--end", default="2026-09-10")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    results = load_team_results(
        start=start,
        end=end,
        results_path=args.results,
        cache_dir=args.cache_dir,
        fetch=args.fetch,
    )
    rows = build_training_rows(results)
    if not rows:
        raise SystemExit("no training rows built")

    reports = evaluate_oof(rows)
    evidence_path = REPO_ROOT / "outputs" / "research" / "game_fit.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(reports, indent=2, sort_keys=True))
    for report in reports:
        print(
            f"{report['month']}: acc={report['winner_accuracy']:.3f} "
            f"brier={report['winner_brier']:.4f} "
            f"margin_mae={report['margin_mae']:.2f} total_mae={report['total_mae']:.2f}"
        )

    engine = fit_engine(rows)
    save_game_engine(args.out, engine)
    print(f"wrote {args.out} ({engine.trained_games} games)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
