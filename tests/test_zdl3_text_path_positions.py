"""Sprint 1 — ZDL-3: on the text path the reconciler's PDF-side position set must
union the regex matches with the extracted position column, so bare sequential
integer positions (which the phantom-safe regex skips) are not lost."""

from __future__ import annotations

from src.ingestion.pdf_parser import _text_path_pdf_positions


def test_sequential_integer_positions_recovered_from_column() -> None:
    # Text layer has no token the position regex would accept as a position
    # (bare integers are excluded by default to avoid phantoms).
    page_texts = ["Benennung Werkstoff\nFormplatte 1.2343\nKern AlSi9Cu3"]
    columns = ["Pos", "Benennung", "Werkstoff"]
    rows = [
        {"Pos": "1", "Benennung": "Formplatte", "Werkstoff": "1.2343"},
        {"Pos": "2", "Benennung": "Kern", "Werkstoff": "AlSi9Cu3"},
        {"Pos": "3", "Benennung": "Schieber", "Werkstoff": "1.2344"},
    ]

    result = _text_path_pdf_positions(page_texts, rows, columns)

    # All three sequential integer positions are present via the column path.
    assert set(result) >= {"1", "2", "3"}


def test_regex_and_column_positions_are_unioned_and_deduped() -> None:
    # Regex picks up the structured "1-1" position from the text; the column
    # repeats it plus adds "1-2". Union must be order-preserving and de-duped.
    page_texts = ["1-1 Formplatte 1.2343"]
    columns = ["Position", "Benennung"]
    rows = [
        {"Position": "1-1", "Benennung": "Formplatte"},
        {"Position": "1-2", "Benennung": "Kern"},
    ]

    result = _text_path_pdf_positions(page_texts, rows, columns)

    assert "1-1" in result
    assert "1-2" in result
    assert len(result) == len(set(result))  # no duplicates


def test_empty_when_no_anchor_column_and_no_regex_hits() -> None:
    page_texts = ["just some prose without positions"]
    columns = ["Benennung", "Werkstoff"]
    rows = [{"Benennung": "Formplatte", "Werkstoff": "1.2343"}]

    assert _text_path_pdf_positions(page_texts, rows, columns) == []
