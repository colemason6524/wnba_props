from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence, Tuple


ARTIFACT_SCHEMA_VERSION = 1

_REQUIRED_KEYS = {
    "schema_version",
    "model_id",
    "prop_type",
    "source_model_version",
    "n_rows",
    "calibration_lambda",
    "pairs",
}


@dataclass(frozen=True)
class ResidualArtifact:
    schema_version: int
    model_id: str
    prop_type: str
    source_model_version: str
    n_rows: int
    calibration_lambda: float
    pairs: Tuple[Tuple[float, float], ...]
    sha256: str
    trained_through: str = ""
    feature_version: str = ""
    fit_at: str = ""
    code_commit: str = ""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_pairs(raw_pairs: object) -> Tuple[Tuple[float, float], ...]:
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ValueError("pairs must be a non-empty list of [minutes_z, rate_error] pairs")
    pairs: list[tuple[float, float]] = []
    for index, pair in enumerate(raw_pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"pair {index} is not a two-element sequence")
        try:
            minutes_z = float(pair[0])
            rate_error = float(pair[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pair {index} is not numeric: {pair!r}") from exc
        pairs.append((minutes_z, rate_error))
    return tuple(pairs)


def load_residual_artifact(path: Path) -> ResidualArtifact:
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        raise ValueError(f"calibration artifact unreadable: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"calibration artifact is not valid JSON: {path}: {exc}") from exc

    missing = _REQUIRED_KEYS - set(payload)
    if missing:
        raise ValueError(f"calibration artifact missing keys: {sorted(missing)}")
    if payload["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            "unsupported artifact schema_version "
            f"{payload['schema_version']!r}; expected {ARTIFACT_SCHEMA_VERSION}"
        )
    for key in ("model_id", "prop_type", "source_model_version"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"{key} must be a non-empty string")

    pairs = _validate_pairs(payload["pairs"])
    n_rows = payload["n_rows"]
    if not isinstance(n_rows, int) or n_rows <= 0:
        raise ValueError("n_rows must be a positive integer")
    if n_rows != len(pairs):
        raise ValueError(f"n_rows {n_rows} does not match pair count {len(pairs)}")

    lambda_value = float(payload["calibration_lambda"])
    if not 0.0 <= lambda_value <= 1.0:
        raise ValueError("calibration_lambda must be within [0, 1]")

    return ResidualArtifact(
        schema_version=int(payload["schema_version"]),
        model_id=str(payload["model_id"]),
        prop_type=str(payload["prop_type"]),
        source_model_version=str(payload["source_model_version"]),
        n_rows=n_rows,
        calibration_lambda=lambda_value,
        pairs=pairs,
        sha256=sha256_file(path),
        trained_through=str(payload.get("trained_through", "")),
        feature_version=str(payload.get("feature_version", "")),
        fit_at=str(payload.get("fit_at", "")),
        code_commit=str(payload.get("code_commit", "")),
    )


def save_residual_artifact(
    path: Path,
    *,
    model_id: str,
    prop_type: str,
    source_model_version: str,
    calibration_lambda: float,
    pairs: Sequence[Tuple[float, float]],
    trained_through: str = "",
    feature_version: str = "wnba-features-v1",
    code_commit: str = "",
) -> ResidualArtifact:
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": model_id,
        "prop_type": prop_type,
        "source_model_version": source_model_version,
        "n_rows": len(pairs),
        "calibration_lambda": round(float(calibration_lambda), 6),
        "trained_through": trained_through,
        "feature_version": feature_version,
        "fit_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": code_commit,
        "pairs": [[round(float(a), 6), round(float(b), 6)] for a, b in pairs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return load_residual_artifact(path)


def calibrate_probability(raw_probability: float, calibration_lambda: float) -> float:
    """Shrink a raw probability toward 0.5."""
    lam = max(0.0, min(1.0, float(calibration_lambda)))
    calibrated = 0.5 + lam * (raw_probability - 0.5)
    return max(0.0, min(1.0, calibrated))


def brier_score(pairs: Iterable[Tuple[float, int]]) -> float:
    observations = [(float(p), int(y)) for p, y in pairs]
    if not observations:
        return 0.0
    return sum((p - y) ** 2 for p, y in observations) / len(observations)


def fit_calibration_lambda(
    pairs: Sequence[Tuple[float, int]],
    candidates: Sequence[float] = (0.5, 0.7, 0.85, 1.0),
) -> float:
    """Choose the shrink factor minimizing Brier score on labeled pairs."""
    if not pairs:
        return 1.0
    best_lambda = 1.0
    best_score = None
    for lam in candidates:
        calibrated = [(calibrate_probability(p, lam), y) for p, y in pairs]
        score = brier_score(calibrated)
        if best_score is None or score < best_score:
            best_score = score
            best_lambda = lam
    return best_lambda
