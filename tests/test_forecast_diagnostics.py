from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_forecast_diagnostics import build_report


class PriceSourceDiagnosticsTests(unittest.TestCase):
    def test_book_groups_attribute_roi_and_keep_unpriced_accuracy_separate(self) -> None:
        rows = [
            {
                "snapshot_id": "s1",
                "phase": "playoff",
                "game_date": "2026-09-30",
                "market": "PTS",
                "subject": "Player A",
                "line": 10.5,
                "source": "fanduel",
                "price_source_book": "fanduel",
                "price_fallback": False,
                "price": -110,
                "probability": 0.6,
                "outcome": "WIN",
                "units": 100 / 110,
                "graded": True,
            },
            {
                "snapshot_id": "s2",
                "phase": "playoff",
                "game_date": "2026-09-30",
                "market": "AST",
                "subject": "Player B",
                "line": 2.5,
                "source": "fanduel",
                "price_source_book": "draftkings",
                "price_fallback": True,
                "price": -120,
                "probability": 0.65,
                "outcome": "LOSS",
                "units": -1.0,
                "graded": True,
            },
            {
                "snapshot_id": "s3",
                "phase": "playoff",
                "game_date": "2026-09-30",
                "market": "REB",
                "subject": "Player C",
                "line": 3.5,
                "source": "fanduel",
                "price": None,
                "probability": 0.6,
                "outcome": "WIN",
                "units": None,
                "graded": True,
            },
            {
                "snapshot_id": "s4",
                "phase": "playoff",
                "game_date": "2026-09-30",
                "market": "ML",
                "subject": "A @ B",
                "source": "bovada",
                "price": -110,
                "probability": 0.6,
                "outcome": "WIN",
                "units": 100 / 110,
                "graded": True,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
            report = build_report(ledger)

        self.assertEqual(
            {"fanduel", "draftkings", "UNPRICED"},
            set(report["by_price_source_book"]),
        )
        books = report["by_price_source_book"]
        self.assertEqual(1, books["fanduel"]["roi"]["plays"])
        self.assertEqual(1, books["draftkings"]["roi"]["losses"])
        self.assertEqual(1, books["draftkings"]["price_fallback_rows"])
        self.assertEqual(1, books["UNPRICED"]["calibration"]["n"])
        self.assertEqual(0, books["UNPRICED"]["roi"]["plays"])
        self.assertEqual(1, report["price_source_book_fallbacks"]["unpriced_rows"])
        self.assertTrue(report["price_source_book_fallbacks"]["has_fallback_rows"])


if __name__ == "__main__":
    unittest.main()
