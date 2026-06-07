"""Tests for the lossless non-data (footer/header/note) row classifier."""

from __future__ import annotations

from src.ingestion.row_classifier import classify_non_data_rows

COLUMNS = ["Pos.", "Benennung", "Werkstoff", "Maße"]


def _rows() -> list[dict[str, str | None]]:
    return [
        {"Pos.": "10", "Benennung": "Kern", "Werkstoff": "1.2343", "Maße": "200x100x40"},
        {"Pos.": "20", "Benennung": "Schieber", "Werkstoff": "1.2344", "Maße": "150x80"},
        # footer
        {"Pos.": "", "Benennung": "Seite 1 von 5", "Werkstoff": "", "Maße": ""},
        # note (no position, single free-text cell)
        {"Pos.": "", "Benennung": "Teile siehe Zeichnung", "Werkstoff": "", "Maße": ""},
        # powered-by footer
        {"Pos.": "", "Benennung": "Powered by Notta.ai", "Werkstoff": "", "Maße": ""},
        # real data row (always kept)
        {"Pos.": "30", "Benennung": "Auswerfer", "Werkstoff": "1.2767", "Maße": "Ø8x150"},
    ]


def test_data_rows_are_never_flagged():
    flags = classify_non_data_rows(_rows(), COLUMNS)
    assert 0 not in flags
    assert 1 not in flags
    assert 5 not in flags


def test_footer_and_note_rows_are_flagged():
    flags = classify_non_data_rows(_rows(), COLUMNS)
    assert 2 in flags and "FOOTER_OR_HEADER_TEXT" in flags[2]
    assert 3 in flags and "SPARSE_ROW" in flags[3]
    assert 4 in flags and "FOOTER_OR_HEADER_TEXT" in flags[4]
    # every flagged row records the missing position as the primary reason
    for reasons in flags.values():
        assert reasons[0] == "NO_POSITION"


def test_position_row_with_sparse_cells_is_not_flagged():
    """The position number wins: a sparse but positioned row stays data."""
    rows = [{"Pos.": "40", "Benennung": "", "Werkstoff": "", "Maße": ""}]
    assert classify_non_data_rows(rows, COLUMNS) == {}


def test_dimension_slash_not_mistaken_for_page_number():
    """'165 / 74' in a full data row must not trigger the page-ratio footer rule."""
    rows = [
        {"Pos.": "50", "Benennung": "Platte", "Werkstoff": "1.2312", "Maße": "165 / 74"}
    ]
    assert classify_non_data_rows(rows, COLUMNS) == {}


def test_controlled_copy_stamp_flagged():
    rows = [{"Pos.": "", "Benennung": "受控文件 副本", "Werkstoff": "", "Maße": ""}]
    flags = classify_non_data_rows(rows, COLUMNS)
    assert 0 in flags and "FOOTER_OR_HEADER_TEXT" in flags[0]


def test_lossless_indices_only_no_mutation():
    """Classifier returns indices into the SAME rows; it never drops/mutates."""
    rows = _rows()
    before = [dict(r) for r in rows]
    flags = classify_non_data_rows(rows, COLUMNS)
    assert rows == before  # input untouched
    assert all(0 <= i < len(rows) for i in flags)  # valid indices only


def test_no_anchor_column_only_footer_text_flagged():
    """Without a position column, only explicit footer text is flagged (sparse
    alone is too weak to risk tagging a real row)."""
    cols = ["Benennung", "Werkstoff"]
    rows = [
        {"Benennung": "Kern", "Werkstoff": ""},  # sparse but no anchor -> not flagged
        {"Benennung": "Seite 2 von 9", "Werkstoff": ""},  # footer text -> flagged
    ]
    flags = classify_non_data_rows(rows, cols)
    assert 0 not in flags
    assert 1 in flags
