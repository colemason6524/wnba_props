from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


ARTIFACT_SCHEMA_VERSION = 1

_REQUIRED_KEYS = {
    "schema_version",
    "residual_model_id",
    "source_model_version",
    "source_config_hash",
    "source_commit",
    "n_rows",
    "projection_ids_sha256",
    "pairs",
}


@dataclass(frozen=True)
class ResidualArtifact:
    schema_version: int
    residual_model_id: str
    source_model_version: str
    source_config_hash: str
    source_commit: str
    n_rows: int
    projection_ids_sha256: str
    pairs: tuple[tuple[float, float], ...]
    sha256: str


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_calibration_artifact(path: Path) -> ResidualArtifact:
    """Load and structurally validate a frozen residual artifact.

    Runtime performs no fitting; this only parses and sanity-checks the
    artifact produced offline by research tooling.
    """
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
    if not isinstance(payload["residual_model_id"], str) or not payload["residual_model_id"]:
        raise ValueError("residual_model_id must be a non-empty string")
    raw_pairs = payload["pairs"]
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

    n_rows = payload["n_rows"]
    if not isinstance(n_rows, int) or n_rows <= 0:
        raise ValueError("n_rows must be a positive integer")
    if n_rows != len(pairs):
        raise ValueError(f"n_rows {n_rows} does not match pair count {len(pairs)}")

    return ResidualArtifact(
        schema_version=int(payload["schema_version"]),
        residual_model_id=str(payload["residual_model_id"]),
        source_model_version=str(payload["source_model_version"]),
        source_config_hash=str(payload["source_config_hash"]),
        source_commit=str(payload["source_commit"]),
        n_rows=n_rows,
        projection_ids_sha256=str(payload["projection_ids_sha256"]),
        pairs=tuple(pairs),
        sha256=sha256_file(path),
    )
