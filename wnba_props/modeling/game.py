from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from ..features.team import TeamFeatures


WINNER_FEATURES = [
    "ppg_diff",
    "opp_allowed_diff",
    "margin_diff",
    "rest_diff",
    "home_flag",
    "season_ppg_diff",
    "pace_avg",
]

MARGIN_FEATURES = [
    "ppg_diff",
    "opp_allowed_diff",
    "margin_diff",
    "rest_diff",
    "home_flag",
    "season_ppg_diff",
    "pace_avg",
]

TOTAL_FEATURES = [
    "ppg_sum",
    "opp_allowed_sum",
    "pace_avg",
    "rest_avg",
]


def _build_vector(features: Sequence[str], values: dict[str, float]) -> list[float]:
    return [values[name] for name in features]


def game_feature_values(home: TeamFeatures, away: TeamFeatures) -> dict[str, float]:
    return {
        "ppg_diff": home.ppg_last_5 - away.ppg_last_5,
        "opp_allowed_diff": home.opp_ppg_allowed_last_5 - away.opp_ppg_allowed_last_5,
        "margin_diff": home.margin_last_5 - away.margin_last_5,
        "rest_diff": float(home.rest_days - away.rest_days),
        "home_flag": 1.0 if home.is_home else 0.0,
        "season_ppg_diff": home.ppg_season - away.ppg_season,
        "pace_avg": 0.5 * (home.pace_proxy_last_5 + away.pace_proxy_last_5),
        "ppg_sum": home.ppg_last_5 + away.ppg_last_5,
        "opp_allowed_sum": home.opp_ppg_allowed_last_5 + away.opp_ppg_allowed_last_5,
        "rest_avg": 0.5 * float(home.rest_days + away.rest_days),
    }


def winner_vector(home: TeamFeatures, away: TeamFeatures) -> list[float]:
    return _build_vector(WINNER_FEATURES, game_feature_values(home, away))


def margin_vector(home: TeamFeatures, away: TeamFeatures) -> list[float]:
    return _build_vector(MARGIN_FEATURES, game_feature_values(home, away))


def total_vector(home: TeamFeatures, away: TeamFeatures) -> list[float]:
    return _build_vector(TOTAL_FEATURES, game_feature_values(home, away))


@dataclass
class RidgeModel:
    coefficients: list[float] = field(default_factory=list)
    intercept: float = 0.0
    residual_sd: float = 0.0
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)

    def _standardize(self, vector: Sequence[float]) -> list[float]:
        if not self.means:
            return list(vector)
        return [
            (value - mean) / scale if scale > 0.0 else 0.0
            for value, mean, scale in zip(vector, self.means, self.scales)
        ]

    def predict(self, vector: Sequence[float]) -> float:
        standardized = self._standardize(vector)
        total = self.intercept
        for coefficient, value in zip(self.coefficients, standardized):
            total += coefficient * value
        return total


@dataclass
class LogisticModel:
    coefficients: list[float] = field(default_factory=list)
    intercept: float = 0.0
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)

    def _standardize(self, vector: Sequence[float]) -> list[float]:
        if not self.means:
            return list(vector)
        return [
            (value - mean) / scale if scale > 0.0 else 0.0
            for value, mean, scale in zip(vector, self.means, self.scales)
        ]

    def predict_proba(self, vector: Sequence[float]) -> float:
        standardized = self._standardize(vector)
        logit = self.intercept
        for coefficient, value in zip(self.coefficients, standardized):
            logit += coefficient * value
        return _sigmoid(logit)


def _standardize_fit(
    rows: Sequence[Sequence[float]],
) -> tuple[list[list[float]], list[float], list[float]]:
    if not rows:
        return [], [], []
    columns = len(rows[0])
    means: list[float] = []
    scales: list[float] = []
    for index in range(columns):
        column = [row[index] for row in rows]
        mean = sum(column) / len(column)
        variance = sum((value - mean) ** 2 for value in column) / len(column)
        scale = math.sqrt(variance) if variance > 0.0 else 1.0
        means.append(mean)
        scales.append(scale)
    standardized = [
        [
            (value - means[index]) / scales[index] if scales[index] > 0.0 else 0.0
            for index, value in enumerate(row)
        ]
        for row in rows
    ]
    return standardized, means, scales


def fit_ridge(
    rows: Sequence[Sequence[float]],
    targets: Sequence[float],
    *,
    l2: float = 1.0,
) -> RidgeModel:
    if not rows:
        return RidgeModel()
    standardized, means, scales = _standardize_fit(rows)
    n = len(standardized)
    k = len(standardized[0])

    design = [[1.0] + list(row) for row in standardized]
    dim = k + 1

    xtx = [[0.0] * dim for _ in range(dim)]
    xty = [0.0] * dim
    for row, target in zip(design, targets):
        for i in range(dim):
            xty[i] += row[i] * target
            for j in range(dim):
                xtx[i][j] += row[i] * row[j]

    for i in range(1, dim):
        xtx[i][i] += l2

    solution = _solve_linear_system(xtx, xty)
    if solution is None:
        return RidgeModel(means=means, scales=scales)

    intercept = solution[0]
    coefficients = solution[1:]

    predictions = [
        intercept + sum(c * v for c, v in zip(coefficients, row))
        for row in standardized
    ]
    residuals = [pred - target for pred, target in zip(predictions, targets)]
    residual_sd = math.sqrt(sum(r * r for r in residuals) / len(residuals)) if residuals else 0.0

    return RidgeModel(
        coefficients=coefficients,
        intercept=intercept,
        residual_sd=residual_sd,
        means=means,
        scales=scales,
    )


def fit_logistic(
    rows: Sequence[Sequence[float]],
    targets: Sequence[int],
    *,
    l2: float = 1.0,
    iterations: int = 500,
    learning_rate: float = 0.1,
) -> LogisticModel:
    if not rows:
        return LogisticModel()
    standardized, means, scales = _standardize_fit(rows)
    n = len(standardized)
    k = len(standardized[0])

    coefficients = [0.0] * k
    intercept = 0.0

    for _ in range(iterations):
        grad_intercept = 0.0
        grads = [0.0] * k
        for row, target in zip(standardized, targets):
            logit = intercept + sum(c * v for c, v in zip(coefficients, row))
            probability = _sigmoid(logit)
            error = probability - target
            grad_intercept += error
            for index in range(k):
                grads[index] += error * row[index]
        intercept -= learning_rate * (grad_intercept / n)
        for index in range(k):
            penalty = l2 * coefficients[index] / n
            coefficients[index] -= learning_rate * (grads[index] / n + penalty)

    return LogisticModel(
        coefficients=coefficients,
        intercept=intercept,
        means=means,
        scales=scales,
    )


def _solve_linear_system(
    matrix: Sequence[Sequence[float]],
    vector: Sequence[float],
) -> Optional[list[float]]:
    size = len(matrix)
    augmented = [list(row) + [vector[i]] for i, row in enumerate(matrix)]

    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        for j in range(column, size + 1):
            augmented[column][j] /= pivot_value
        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column]
            for j in range(column, size + 1):
                augmented[row_index][j] -= factor * augmented[column][j]

    return [augmented[i][size] for i in range(size)]


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _normal_cdf(value: float, mean: float, sd: float) -> float:
    if sd <= 0.0:
        return 1.0 if value >= mean else 0.0
    z = (value - mean) / sd
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
