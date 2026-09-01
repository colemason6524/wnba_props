"""Walk-forward evaluation harness for projection experiments.

Uses only cached, point-in-time gamelog rows (Basketball-Reference parsed
cache). Any fitted parameter is fit on the training period only and frozen
before the evaluation period. Results are written under outputs/research/.

Usage:
    python3 research/walkforward.py [--log-dir outputs/hunt/windows_cache/shared]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.stats import cluster_bootstrap  # noqa: E402

TRAIN_END = date(2026, 7, 31)
EVAL_START = date(2026, 8, 1)
MIN_HISTORY = 3
RECENCY_HALF_LIFE = 6.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_rows(log_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(log_dir.glob("bref_parsed_gamelog_*_2026_v2.json")):
        match = re.search(r"bref_parsed_gamelog_(.+?)_2026_v2", path.name)
        if not match:
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            continue
        for row in data:
            if not row.get("did_play"):
                continue
            try:
                row = dict(row)
                row["player_code"] = match.group(1)
                row["date"] = date.fromisoformat(str(row["game_date"]))
                rows.append(row)
            except (KeyError, ValueError):
                continue
    deduped: dict[tuple, dict] = {}
    for row in rows:
        key = (row["player_code"], row["date"], row.get("team", ""), row.get("opponent", ""))
        deduped.setdefault(key, row)
    return sorted(deduped.values(), key=lambda row: (row["player_code"], row["date"]))


def build_dataset(rows: list[dict]) -> list[dict]:
    """One row per player-game with strictly-prior history attached."""
    by_player: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_player[row["player_code"]].append(row)
    dataset = []
    for player_code, player_rows in by_player.items():
        for index, target in enumerate(player_rows):
            history = player_rows[:index]
            if len(history) < MIN_HISTORY:
                continue
            dataset.append(
                {
                    "player_code": player_code,
                    "date": target["date"],
                    "team": target.get("team", ""),
                    "minutes": float(target.get("minutes", 0.0)),
                    "points": float(target.get("points", 0.0)),
                    "history": history,
                }
            )
    return dataset


# ---------------------------------------------------------------------------
# Predictors (history -> prediction)
# ---------------------------------------------------------------------------

def recent_mean(history: list[dict], field: str, window: int) -> float:
    values = [float(row.get(field, 0.0)) for row in history[-window:]]
    return mean(values) if values else 0.0


def season_mean(history: list[dict], field: str) -> float:
    values = [float(row.get(field, 0.0)) for row in history]
    return mean(values) if values else 0.0


def recency_weighted_mean(history: list[dict], field: str, eval_date: date, half_life: float) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for row in history:
        age_days = max(0, (eval_date - row["date"]).days)
        weight = 0.5 ** (age_days / half_life)
        weighted_sum += weight * float(row.get(field, 0.0))
        weight_total += weight
    return weighted_sum / weight_total if weight_total else 0.0


MINUTE_MODELS = {
    "minutes_l5": lambda history, eval_date: recent_mean(history, "minutes", 5),
    "minutes_recency": lambda history, eval_date: recency_weighted_mean(history, "minutes", eval_date, RECENCY_HALF_LIFE),
    "minutes_season": lambda history, eval_date: season_mean(history, "minutes"),
}

POINTS_MODELS = {
    "points_l5": lambda history, eval_date: recent_mean(history, "points", 5),
    "points_season": lambda history, eval_date: season_mean(history, "points"),
}


def make_blend(weight: float):
    def predict(history: list[dict], eval_date: date) -> float:
        projected_minutes = (
            weight * recency_weighted_mean(history, "minutes", eval_date, RECENCY_HALF_LIFE)
            + (1 - weight) * season_mean(history, "minutes")
        )
        projected_ppm = (
            weight * recency_ppm(history, eval_date, RECENCY_HALF_LIFE)
            + (1 - weight) * season_ppm(history)
        )
        return projected_minutes * projected_ppm

    return predict


def recency_ppm(history: list[dict], eval_date: date, half_life: float) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for row in history:
        minutes = float(row.get("minutes", 0.0))
        if minutes <= 0:
            continue
        age_days = max(0, (eval_date - row["date"]).days)
        weight = 0.5 ** (age_days / half_life)
        weighted_sum += weight * (float(row.get("points", 0.0)) / minutes)
        weight_total += weight
    return weighted_sum / weight_total if weight_total else 0.0


def season_ppm(history: list[dict]) -> float:
    points = sum(float(row.get("points", 0.0)) for row in history)
    minutes = sum(float(row.get("minutes", 0.0)) for row in history)
    return points / minutes if minutes > 0 else 0.0


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def mae_statistic(field: str):
    def statistic(sample: list[dict]) -> float:
        if not sample:
            return 0.0
        return mean(abs(row["actual"] - row["prediction"]) for row in sample)

    return statistic


def evaluate(dataset: list[dict], predict, target_field: str, eval_start: date) -> list[dict]:
    scored = []
    for row in dataset:
        if row["date"] < eval_start:
            continue
        prediction = predict(row["history"], row["date"])
        scored.append(
            {
                "player_code": row["player_code"],
                "date": row["date"],
                "prediction": prediction,
                "actual": row[target_field],
            }
        )
    return scored


def fit_blend_weight(train_rows: list[dict]) -> float:
    best_weight = 0.65
    best_mae = float("inf")
    for weight in [round(0.05 * step, 2) for step in range(1, 20)]:
        errors = []
        for row in train_rows:
            prediction = make_blend(weight)(row["history"], row["date"])
            errors.append(abs(row["points"] - prediction))
        candidate_mae = mean(errors) if errors else float("inf")
        if candidate_mae < best_mae:
            best_mae = candidate_mae
            best_weight = weight
    return best_weight


def dnp_rate_by_availability(rows: list[dict], eval_start: date) -> dict:
    """Next-team-game played rate bucketed by games in the player's last 10 rows.

    Parsed rows contain only played games, so a player with no row on a date
    their team played is treated as absent (DNP or not in cache — the bucket
    rates are therefore diagnostic, not exact).
    """
    team_games: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        team_games[row.get("team", "")].add(row["date"])
    by_player: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_player[row["player_code"]].append(row)

    buckets: dict[str, list[int]] = defaultdict(list)
    for player_rows in by_player.values():
        for index in range(5, len(player_rows)):
            target = player_rows[index]
            if target["date"] < eval_start:
                continue
            history = player_rows[:index]
            last_played = history[-1]["date"]
            bucket = str(min(10, len(history[-10:])))
            prior_team_games = [
                game_date
                for game_date in team_games.get(target.get("team", ""), set())
                if last_played < game_date < target["date"]
            ]
            if prior_team_games:
                buckets[bucket].append(0)
            else:
                buckets[bucket].append(1)
    rates = {}
    for bucket, outcomes in sorted(buckets.items()):
        rates[bucket] = {"opportunities": len(outcomes), "played_rate": mean(outcomes)}
    return rates


def run_experiments(log_dir: Path, draws: int = 2000, seed: int = 20260901) -> dict:
    rows = load_rows(log_dir)
    dataset = build_dataset(rows)
    train_rows = [row for row in dataset if row["date"] <= TRAIN_END]
    eval_rows = [row for row in dataset if row["date"] >= EVAL_START]

    results: dict = {
        "data": {
            "rows": len(rows),
            "players": len({row["player_code"] for row in rows}),
            "train_rows": len(train_rows),
            "eval_rows": len(eval_rows),
            "eval_dates": [str(min(row["date"] for row in eval_rows)), str(max(row["date"] for row in eval_rows))] if eval_rows else [],
        },
        "predeclared": {
            "train_end": TRAIN_END.isoformat(),
            "eval_start": EVAL_START.isoformat(),
            "min_history": MIN_HISTORY,
            "recency_half_life_days": RECENCY_HALF_LIFE,
            "clustering": "game_date",
        },
    }

    minute_results = {}
    for name, model in MINUTE_MODELS.items():
        scored = evaluate(dataset, model, "minutes", EVAL_START)
        statistic = mae_statistic("minutes")
        boot = cluster_bootstrap(
            scored,
            cluster_of=lambda row: row["date"],
            statistic=statistic,
            draws=draws,
            seed=seed,
        )
        minute_results[name] = {"mae": boot["point"], "ci95": [boot["low"], boot["high"]], "n": len(scored)}
    results["minutes_models"] = minute_results

    blend_weight = fit_blend_weight(train_rows)
    results["blend_weight_fit_on_train"] = blend_weight

    points_results = {}
    for name, model in POINTS_MODELS.items():
        scored = evaluate(dataset, model, "points", EVAL_START)
        statistic = mae_statistic("points")
        boot = cluster_bootstrap(
            scored,
            cluster_of=lambda row: row["date"],
            statistic=statistic,
            draws=draws,
            seed=seed,
        )
        points_results[name] = {"mae": boot["point"], "ci95": [boot["low"], boot["high"]], "n": len(scored)}
    blend_scored = evaluate(dataset, make_blend(blend_weight), "points", EVAL_START)
    boot = cluster_bootstrap(
        blend_scored,
        cluster_of=lambda row: row["date"],
        statistic=mae_statistic("points"),
        draws=draws,
        seed=seed,
    )
    points_results["minutes_x_rate_blend"] = {
        "mae": boot["point"],
        "ci95": [boot["low"], boot["high"]],
        "n": len(blend_scored),
        "weight": blend_weight,
    }
    results["points_models"] = points_results
    results["dnp_availability"] = dnp_rate_by_availability(rows, EVAL_START)
    return results


def render_report(results: dict) -> str:
    lines = ["WALK-FORWARD DIAGNOSTICS (train <= %s, eval >= %s)" % (results["predeclared"]["train_end"], results["predeclared"]["eval_start"])]
    data = results["data"]
    lines.append(f"Rows: {data['rows']} | players: {data['players']} | train: {data['train_rows']} | eval: {data['eval_rows']} | eval window: {data['eval_dates']}")
    lines.append("")
    lines.append("Next-game minutes MAE (lower is better):")
    for name, stats in sorted(results["minutes_models"].items(), key=lambda item: item[1]["mae"]):
        lines.append(f"- {name}: {stats['mae']:.3f} (CI95 {stats['ci95'][0]:.3f}..{stats['ci95'][1]:.3f}, n={stats['n']})")
    lines.append("")
    lines.append("Next-game points MAE (lower is better):")
    for name, stats in sorted(results["points_models"].items(), key=lambda item: item[1]["mae"]):
        weight = f" (w={stats['weight']})" if "weight" in stats else ""
        lines.append(f"- {name}: {stats['mae']:.3f} (CI95 {stats['ci95'][0]:.3f}..{stats['ci95'][1]:.3f}, n={stats['n']}){weight}")
    lines.append("")
    lines.append("Played-rate by games in last 10 (DNP mass diagnostic):")
    for bucket, stats in results["dnp_availability"].items():
        lines.append(f"- last10={bucket}: played rate {stats['played_rate']:.3f} across {stats['opportunities']} opportunities")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-dir",
        default="outputs/hunt/windows_cache/shared",
        help="Directory containing bref_parsed_gamelog_*_2026_v2.json files",
    )
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260901)
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    results = run_experiments(log_dir, draws=args.draws, seed=args.seed)
    report = render_report(results)
    print(report)

    out_dir = REPO_ROOT / "outputs" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    (out_dir / f"walkforward_{stamp}.json").write_text(json.dumps(results, indent=2, default=str))
    (out_dir / f"walkforward_{stamp}.txt").write_text(report)
    print(f"Saved: outputs/research/walkforward_{stamp}.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
