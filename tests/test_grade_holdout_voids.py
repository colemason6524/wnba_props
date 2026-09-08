"""Void (DNP) rows must not be counted as losses in the holdout grader's W-L."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GRADER_PATH = Path(__file__).resolve().parents[1] / "outputs" / "hunt" / "grade_holdout.py"


@pytest.fixture(scope="module")
def grader():
    spec = importlib.util.spec_from_file_location("grade_holdout", GRADER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(status: str, outcome: str | None, american: int = -110, prop_type: str = "PTS", side: str = "OVER") -> dict:
    return {
        "status": status,
        "outcome": outcome,
        "american_odds": american,
        "prop_type": prop_type,
        "side": side,
        "screen_date": "2026-08-10",
    }


WIN = _row("resolved", "win", american=-110)
LOSS = _row("resolved", "loss", american=-110)
VOID = _row("void_dnp", None, american=-110)
PUSH = _row("resolved", "push", american=-110)


def test_tally_record_separates_voids(grader):
    priced = [(WIN, grader.profit_units(WIN)), (LOSS, -1.0), (VOID, 0.0)]
    record = grader.tally_record(priced)
    assert (record["wins"], record["losses"], record["voids"], record["pushes"]) == (1, 1, 1, 0)
    assert record["decided"] == 2
    assert record["hit_rate"] == pytest.approx(0.5)


def test_summarize_policy_win_loss_void(grader):
    summary = grader.summarize_policy([WIN, LOSS, VOID])
    assert summary["n"] == 3
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["voids"] == 1
    assert summary["pushes"] == 0
    assert summary["decided"] == 2
    assert summary["hit_rate"] == pytest.approx(0.5)
    # Void carries 0u: units are unchanged by the record fix.
    assert summary["units"] == pytest.approx(100.0 / 110.0 - 1.0)
    assert grader.record_label(summary) == "1-1 (void 1)"


def test_push_is_not_a_void_or_loss(grader):
    summary = grader.summarize_policy([WIN, PUSH, VOID])
    assert (summary["wins"], summary["losses"], summary["pushes"], summary["voids"]) == (1, 0, 1, 1)
    assert summary["hit_rate"] == pytest.approx(1.0)
    assert grader.record_label(summary) == "1-0-1p (void 1)"


def test_dnp_as_loss_sensitivity_counts_void_as_loss(grader):
    summary = grader.summarize_policy([WIN, LOSS, VOID], dnp_as_loss=True)
    assert (summary["wins"], summary["losses"], summary["voids"]) == (1, 2, 0)
    assert summary["units"] == pytest.approx(100.0 / 110.0 - 2.0)


def test_split_table_uses_void_aware_record(grader):
    rows = [WIN, LOSS, VOID, _row("resolved", "win", side="UNDER")]
    lines = grader.split_table(rows, lambda row: row["side"], "By side")
    assert lines[0] == "By side:"
    over_line = next(line for line in lines if line.startswith("- OVER"))
    assert "record 1-1 (void 1)" in over_line
    assert "hit 50.0%" in over_line
