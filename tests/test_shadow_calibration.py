from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wnba_props.shadow.calibration import load_calibration_artifact


def _payload(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "residual_model_id": "v1-joint-residual-r1",
        "source_model_version": "wnba-points-shadow-v1",
        "source_config_hash": "b3096ccb93b6f4d1",
        "source_commit": "3184273954dd0eaba7966d8a791093633fbf3edf",
        "n_rows": 2,
        "projection_ids_sha256": "0" * 64,
        "pairs": [[0.5, 0.01], [-0.5, -0.01]],
    }
    payload.update(overrides)
    return payload


class ShadowCalibrationTests(unittest.TestCase):
    def test_valid_artifact_loads_with_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(_payload()))

            artifact = load_calibration_artifact(path)

            self.assertEqual(1, artifact.schema_version)
            self.assertEqual("v1-joint-residual-r1", artifact.residual_model_id)
            self.assertEqual(2, artifact.n_rows)
            self.assertEqual(2, len(artifact.pairs))
            self.assertEqual((0.5, 0.01), artifact.pairs[0])
            self.assertEqual(64, len(artifact.sha256))

    def test_same_content_produces_same_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "a.json"
            second = Path(directory) / "b.json"
            first.write_text(json.dumps(_payload()))
            second.write_text(json.dumps(_payload()))

            self.assertEqual(
                load_calibration_artifact(first).sha256,
                load_calibration_artifact(second).sha256,
            )

    def test_missing_file_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                load_calibration_artifact(Path(directory) / "missing.json")

    def test_invalid_json_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text("{not json")
            with self.assertRaises(ValueError):
                load_calibration_artifact(path)

    def test_missing_keys_raise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            payload = _payload()
            del payload["pairs"]
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "missing keys"):
                load_calibration_artifact(path)

    def test_unsupported_schema_version_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(_payload(schema_version=99)))
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_calibration_artifact(path)

    def test_row_count_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(_payload(n_rows=7)))
            with self.assertRaisesRegex(ValueError, "does not match pair count"):
                load_calibration_artifact(path)

    def test_non_numeric_pair_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(_payload(pairs=[[0.1, "fast"], [-0.1, 0.0]])))
            with self.assertRaisesRegex(ValueError, "not numeric"):
                load_calibration_artifact(path)

    def test_empty_pairs_raise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(_payload(n_rows=0, pairs=[])))
            with self.assertRaisesRegex(ValueError, "non-empty list"):
                load_calibration_artifact(path)


if __name__ == "__main__":
    unittest.main()
