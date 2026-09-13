"""Runtime artifact registry with MLB-style health gates.

All model artifacts are fitted offline and loaded as frozen production inputs.
A missing, malformed, or mismatched artifact fails the run rather than
silently publishing a partial board.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from ..config import ROOT
from .calibration import ResidualArtifact, load_residual_artifact
from .game_forecast import GameEngine, load_game_engine

ARTIFACT_DIR = ROOT / "wnba_props" / "artifacts"
GAME_ARTIFACT_NAME = "game_engine_artifact.json"
PROP_TYPES: Tuple[str, ...] = ("PTS", "REB", "AST", "3PM")


class ArtifactError(RuntimeError):
    """Raised when a required production artifact is missing or invalid."""


@dataclass
class ArtifactBundle:
    game: GameEngine
    residuals: Dict[str, ResidualArtifact]
    artifact_dir: str
    loaded_at: str

    def versions(self) -> dict:
        return {
            "game_engine_version": self.game.version,
            "game_engine_sha256": self.game.sha256,
            "game_engine_trained_through": self.game.trained_through,
            "game_engine_trained_games": self.game.trained_games,
            "prop_artifacts": {
                prop_type: {
                    "model_id": artifact.model_id,
                    "sha256": artifact.sha256,
                    "n_rows": artifact.n_rows,
                    "calibration_lambda": artifact.calibration_lambda,
                    "trained_through": artifact.trained_through,
                }
                for prop_type, artifact in self.residuals.items()
            },
        }


def load_artifacts(
    artifact_dir: Path = ARTIFACT_DIR,
    prop_types: Tuple[str, ...] = PROP_TYPES,
) -> ArtifactBundle:
    game_path = artifact_dir / GAME_ARTIFACT_NAME
    if not game_path.exists():
        raise ArtifactError(f"missing game artifact: {game_path}")
    try:
        game = load_game_engine(game_path)
    except Exception as exc:  # noqa: BLE001
        raise ArtifactError(f"invalid game artifact {game_path}: {exc}") from exc

    residuals: Dict[str, ResidualArtifact] = {}
    missing = []
    for prop_type in prop_types:
        path = artifact_dir / f"{prop_type.lower()}_engine_artifact.json"
        if not path.exists():
            missing.append(prop_type)
            continue
        try:
            artifact = load_residual_artifact(path)
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"invalid {prop_type} artifact {path}: {exc}") from exc
        if artifact.prop_type.upper() != prop_type.upper():
            raise ArtifactError(
                f"{prop_type} artifact prop_type mismatch: {artifact.prop_type}"
            )
        residuals[prop_type.upper()] = artifact

    if missing:
        raise ArtifactError(f"missing prop artifacts: {sorted(missing)}")

    return ArtifactBundle(
        game=game,
        residuals=residuals,
        artifact_dir=str(artifact_dir),
        loaded_at=datetime.now(timezone.utc).isoformat(),
    )


def code_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"
