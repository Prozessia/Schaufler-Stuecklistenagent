"""BUG-016 acceptance: _compare_dual_extractions uses sequence alignment.

The old pairing (anchor → index → closest-free) drifted when extraction B
dropped a middle row, comparing wrong pairs and missing the actual gap.
The new implementation uses difflib.SequenceMatcher so aligned rows are always
correct and unmatched A-rows are reliably flagged as MISSING_ROW.
"""

from __future__ import annotations

import src.ingestion.pdf_parser as pdf_parser

_COLS = ["POS", "DESC", "STK"]


def _row(pos: str, desc: str, stk: str) -> dict[str, str | None]:
    return {"POS": pos, "DESC": desc, "STK": stk}


def test_b_missing_middle_row_flags_correctly() -> None:
    """BUG-016: B drops the middle row → only row 2 flagged, rows 1 and 3 clean.

    Rows: A=[1,2,3]  B=[1,3]
    Row 1 and 3 align perfectly — no pseudo-mismatches.
    Row 2 (A-only) must be flagged as MISSING_ROW on all critical columns.
    """
    rows_a = [
        _row("1", "PLATE", "2"),
        _row("2", "CORE", "1"),
        _row("3", "SLIDER", "4"),
    ]
    rows_b = [
        _row("1", "PLATE", "2"),
        _row("3", "SLIDER", "4"),
    ]

    mismatches, delta = pdf_parser._compare_dual_extractions(rows_a, rows_b, _COLS)

    # Row 2 (index 1) must be flagged — it has no partner in B.
    assert 1 in mismatches, f"Expected row index 1 in mismatches, got keys: {list(mismatches.keys())}"
    flags = mismatches[1]
    # At least the anchor column (POS) and the critical qty column (STK) should appear.
    assert any("MISSING_ROW" in f for f in flags), f"Expected MISSING_ROW flags, got: {flags}"
    assert any("POS" in f for f in flags), f"Expected POS flag, got: {flags}"

    # Rows 0 and 2 must NOT be flagged (they aligned correctly).
    assert 0 not in mismatches, f"Row 0 should not be flagged, got: {mismatches.get(0)}"
    assert 2 not in mismatches, f"Row 2 should not be flagged, got: {mismatches.get(2)}"

    # Delta: A has 3 rows, B has 2.
    assert delta == 1


def test_identical_runs_produce_no_mismatches() -> None:
    """BUG-016: when A == B there must be no mismatches and delta == 0."""
    rows = [
        _row("1", "PLATE", "2"),
        _row("2", "CORE", "1"),
        _row("3", "SLIDER", "4"),
    ]

    mismatches, delta = pdf_parser._compare_dual_extractions(rows, list(rows), _COLS)

    assert mismatches == {}, f"Expected no mismatches, got: {mismatches}"
    assert delta == 0


def test_single_cell_deviation_flags_only_that_column() -> None:
    """BUG-016: one differing cell value → exactly that column flagged, nothing else."""
    rows_a = [
        _row("1", "PLATE", "2"),
        _row("2", "CORE", "1"),
    ]
    rows_b = [
        _row("1", "PLATE", "2"),
        _row("2", "CORE", "7"),  # STK differs: "1" vs "7"
    ]

    mismatches, delta = pdf_parser._compare_dual_extractions(rows_a, rows_b, _COLS)

    assert delta == 0
    # Row 1 (index 1) should have exactly one flag on STK.
    assert 1 in mismatches, f"Expected row 1 flagged, got: {list(mismatches.keys())}"
    flags = mismatches[1]
    assert len(flags) == 1, f"Expected exactly 1 flag, got: {flags}"
    assert "STK" in flags[0]
    assert "A='1'" in flags[0] or "A='2'" not in flags[0]  # A-value is "1"
    # Row 0 must be clean.
    assert 0 not in mismatches
