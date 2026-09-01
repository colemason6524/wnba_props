"""Clustered-bootstrap statistics for correlated betting rows.

Props from the same slate/game share outcomes, so row-level confidence
intervals overstate certainty. These helpers treat the slate (or game) as
the independent unit and resample clusters with replacement.
"""
from __future__ import annotations

import random
from statistics import mean
from typing import Callable, Hashable, Iterable, Sequence

ClusterKey = Hashable


def percentile_interval(values: Sequence[float], alpha: float = 0.05) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    ordered = sorted(values)
    low_index = int((alpha / 2.0) * len(ordered))
    high_index = min(len(ordered) - 1, int((1 - alpha / 2.0) * len(ordered)))
    return ordered[low_index], ordered[high_index]


def bootstrap_mean(values: Sequence[float], draws: int = 10_000, seed: int = 0, alpha: float = 0.05) -> dict:
    """IID bootstrap over values; use only when rows are genuinely independent."""
    rng = random.Random(seed)
    if not values:
        return {"mean": 0.0, "low": 0.0, "high": 0.0, "n": 0}
    samples = []
    for _ in range(draws):
        samples.append(mean(rng.choice(values) for _ in range(len(values))))
    low, high = percentile_interval(samples, alpha)
    return {"mean": mean(values), "low": low, "high": high, "n": len(values)}


def cluster_bootstrap(
    rows: Sequence,
    cluster_of: Callable[[object], ClusterKey],
    statistic: Callable[[Sequence], float],
    draws: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    """Resample clusters with replacement; statistic is recomputed per draw."""
    clusters: dict[ClusterKey, list] = {}
    for row in rows:
        clusters.setdefault(cluster_of(row), []).append(row)
    if not clusters:
        return {"point": 0.0, "low": 0.0, "high": 0.0, "n_clusters": 0, "n_rows": 0}
    keys = sorted(clusters, key=str)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        resampled_rows: list = []
        for _ in range(len(keys)):
            resampled_rows.extend(clusters[rng.choice(keys)])
        samples.append(statistic(resampled_rows))
    low, high = percentile_interval(samples, alpha)
    return {
        "point": statistic(list(rows)),
        "low": low,
        "high": high,
        "n_clusters": len(keys),
        "n_rows": len(rows),
    }


def paired_cluster_diff(
    rows_a: Sequence,
    rows_b: Sequence,
    cluster_of: Callable[[object], ClusterKey],
    statistic: Callable[[Sequence], float],
    draws: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    """Paired difference of two policies over identical cluster resamples."""
    clusters_a: dict[ClusterKey, list] = {}
    clusters_b: dict[ClusterKey, list] = {}
    for row in rows_a:
        clusters_a.setdefault(cluster_of(row), []).append(row)
    for row in rows_b:
        clusters_b.setdefault(cluster_of(row), []).append(row)
    keys = sorted(set(clusters_a) | set(clusters_b), key=str)
    if not keys:
        return {"diff": 0.0, "low": 0.0, "high": 0.0, "n_clusters": 0}
    rng = random.Random(seed)
    diffs = []
    for _ in range(draws):
        sample_a: list = []
        sample_b: list = []
        for _ in range(len(keys)):
            key = rng.choice(keys)
            sample_a.extend(clusters_a.get(key, []))
            sample_b.extend(clusters_b.get(key, []))
        diffs.append(statistic(sample_a) - statistic(sample_b))
    low, high = percentile_interval(diffs, alpha)
    all_a = [row for rows in clusters_a.values() for row in rows]
    all_b = [row for rows in clusters_b.values() for row in rows]
    return {
        "diff": statistic(all_a) - statistic(all_b) if all_a and all_b else 0.0,
        "low": low,
        "high": high,
        "n_clusters": len(keys),
    }


def leave_one_cluster_out(
    rows: Sequence,
    cluster_of: Callable[[object], ClusterKey],
    statistic: Callable[[Sequence], float],
) -> list[tuple[ClusterKey, float]]:
    clusters: dict[ClusterKey, list] = {}
    for row in rows:
        clusters.setdefault(cluster_of(row), []).append(row)
    results = []
    for key in sorted(clusters, key=str):
        subset = [row for other_key, rows in clusters.items() if other_key != key for row in rows]
        results.append((key, statistic(subset)))
    return results


def flat_stake_roi(rows: Iterable[dict], units_of: Callable[[dict], float | None]) -> Callable[[Sequence], float]:
    """Build a clustered-bootstrap statistic: flat-stake ROI over priced rows."""

    def statistic(sample: Sequence) -> float:
        units = [units_of(row) for row in sample]
        priced = [value for value in units if value is not None]
        if not priced:
            return 0.0
        return sum(priced) / len(priced)

    return statistic


def hit_rate(rows: Iterable[dict], outcome_of: Callable[[dict], str]) -> Callable[[Sequence], float]:
    def statistic(sample: Sequence) -> float:
        decided = [outcome_of(row) for row in sample if outcome_of(row) != "push"]
        if not decided:
            return 0.0
        return sum(1 for outcome in decided if outcome == "win") / len(decided)

    return statistic
