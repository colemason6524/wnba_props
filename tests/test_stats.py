from __future__ import annotations

import unittest

from wnba_props.stats import (
    bootstrap_mean,
    cluster_bootstrap,
    flat_stake_roi,
    hit_rate,
    leave_one_cluster_out,
    paired_cluster_diff,
    percentile_interval,
)


def make_rows():
    rows = []
    for slate in range(10):
        for index in range(4):
            rows.append(
                {
                    "slate": slate,
                    "player": f"p{slate}-{index}",
                    "outcome": "win" if (slate + index) % 3 else "loss",
                    "units": 0.91 if (slate + index) % 3 else -1.0,
                }
            )
    return rows


class PercentileTests(unittest.TestCase):
    def test_interval_bounds(self) -> None:
        low, high = percentile_interval(list(range(1001)))
        self.assertEqual(25, low)
        self.assertEqual(975, high)

    def test_empty_values(self) -> None:
        self.assertEqual((0.0, 0.0), percentile_interval([]))


class BootstrapMeanTests(unittest.TestCase):
    def test_mean_and_interval(self) -> None:
        result = bootstrap_mean([1.0] * 50 + [3.0] * 50, draws=2000, seed=7)
        self.assertAlmostEqual(2.0, result["mean"])
        self.assertLess(result["low"], 2.0)
        self.assertGreater(result["high"], 2.0)

    def test_empty(self) -> None:
        self.assertEqual(0, bootstrap_mean([])["n"])


class ClusterBootstrapTests(unittest.TestCase):
    def test_clusters_are_resampled_not_rows(self) -> None:
        rows = make_rows()
        roi = flat_stake_roi(rows, units_of=lambda row: row["units"])
        result = cluster_bootstrap(rows, cluster_of=lambda row: row["slate"], statistic=roi, draws=500, seed=3)
        self.assertEqual(10, result["n_clusters"])
        self.assertEqual(40, result["n_rows"])
        self.assertAlmostEqual(result["point"], roi(rows))

    def test_hit_rate_statistic(self) -> None:
        rows = make_rows()
        rate = hit_rate(rows, outcome_of=lambda row: row["outcome"])
        result = cluster_bootstrap(rows, cluster_of=lambda row: row["slate"], statistic=rate, draws=500, seed=3)
        self.assertAlmostEqual(0.65, result["point"], places=2)


class PairedDiffTests(unittest.TestCase):
    def test_identical_policies_have_zero_diff(self) -> None:
        rows = make_rows()
        roi = flat_stake_roi(rows, units_of=lambda row: row["units"])
        result = paired_cluster_diff(
            rows, rows,
            cluster_of=lambda row: row["slate"],
            statistic=roi,
            draws=300,
            seed=11,
        )
        self.assertAlmostEqual(0.0, result["diff"])
        self.assertEqual(0.0, result["low"])
        self.assertEqual(0.0, result["high"])

    def test_positive_policy_shift(self) -> None:
        rows_a = make_rows()
        rows_b = [row | {"units": row["units"] + 0.5} for row in make_rows()]
        roi = flat_stake_roi(rows_a, units_of=lambda row: row["units"])
        result = paired_cluster_diff(
            rows_a, rows_b,
            cluster_of=lambda row: row["slate"],
            statistic=roi,
            draws=300,
            seed=11,
        )
        self.assertAlmostEqual(-0.5, result["diff"], places=6)


class LeaveOneClusterOutTests(unittest.TestCase):
    def test_one_result_per_cluster(self) -> None:
        rows = make_rows()
        roi = flat_stake_roi(rows, units_of=lambda row: row["units"])
        results = leave_one_cluster_out(rows, cluster_of=lambda row: row["slate"], statistic=roi)
        self.assertEqual(10, len(results))
        for key, _ in results:
            self.assertIsInstance(key, int)


if __name__ == "__main__":
    unittest.main()
