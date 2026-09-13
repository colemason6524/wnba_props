from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wnba_props.modeling.calibration import save_residual_artifact
from wnba_props.modeling.game import fit_logistic, fit_ridge
from wnba_props.modeling.game_forecast import GameEngine, save_game_engine
from wnba_props.modeling.registry import ArtifactError, load_artifacts


def _write_all(artifact_dir: Path) -> None:
    save_game_engine(
        artifact_dir / "game_engine_artifact.json",
        GameEngine(
            winner=fit_logistic([[1.0], [-1.0]], [1, 0], l2=0.0, iterations=100),
            margin=fit_ridge([[1.0], [-1.0]], [5.0, -5.0], l2=0.0),
            total=fit_ridge([[1.0], [-1.0]], [170.0, 150.0], l2=0.0),
            trained_games=2,
            trained_through="2026-08-30",
        ),
    )
    for prop_type in ("PTS", "REB", "AST", "3PM"):
        save_residual_artifact(
            artifact_dir / f"{prop_type.lower()}_engine_artifact.json",
            model_id="m",
            prop_type=prop_type,
            source_model_version="v2",
            calibration_lambda=1.0,
            pairs=[(0.0, 0.0)],
            trained_through="2026-08-30",
        )


class RegistryTests(unittest.TestCase):
    def test_load_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            _write_all(artifact_dir)
            bundle = load_artifacts(artifact_dir)
        self.assertEqual(set(bundle.residuals), {"PTS", "REB", "AST", "3PM"})
        self.assertEqual(bundle.game.trained_through, "2026-08-30")
        versions = bundle.versions()
        self.assertIn("PTS", versions["prop_artifacts"])
        self.assertNotEqual(versions["game_engine_sha256"], "")

    def test_missing_game_artifact_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ArtifactError):
                load_artifacts(Path(tmp))

    def test_missing_prop_artifact_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            _write_all(artifact_dir)
            (artifact_dir / "ast_engine_artifact.json").unlink()
            with self.assertRaises(ArtifactError):
                load_artifacts(artifact_dir)


if __name__ == "__main__":
    unittest.main()
