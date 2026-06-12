"""Unit tests for PLAUS: flag parsing in _index_row_validation_flags (BUG-021)."""

from __future__ import annotations

from src.scoring.ensemble_scorer import _index_row_validation_flags
from src.core.models import (
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceMetadata,
    TransformationResult,
    TransformedRow,
    CellTransformation,
)


def _make_transform_result(row_validation_flags: dict) -> TransformationResult:
    """Build a minimal TransformationResult with given row_validation_flags."""
    bom = ParsedBOM(
        source=SourceMetadata(
            filename="test.pdf",
            filepath="test.pdf",
            customer="Test",
            format=FileFormat.PDF,
        ),
        headers=["Menge"],
        rows=[{"Menge": "70000"}],
    )
    return TransformationResult(
        source_file="test.pdf",
        customer="Test",
        source_is_pdf=True,
        rows=[
            TransformedRow(
                row_index=0,
                cells=[
                    CellTransformation(
                        source_column="Menge",
                        target_field="Menge",
                        target_column="C",
                        raw_value="70000",
                        transformed_value="70000",
                        method="direct",
                        confidence=0.9,
                    )
                ],
            )
        ],
        row_validation_flags=row_validation_flags,
    )


def test_plaus_flag_is_indexed_to_correct_cell() -> None:
    """PLAUS:Menge: value 70000 above maximum 999 → {(0, 'Menge')} in plaus_cells."""
    flags = {0: ["PLAUS:Menge: value 70000 above maximum 999"]}
    result = _make_transform_result(flags)
    _, _, _, plaus_cells = _index_row_validation_flags(result)
    assert (0, "Menge") in plaus_cells


def test_plaus_flag_not_confused_with_other_prefixes() -> None:
    """COORDMISS, DUAL and PLAUS flags are routed to separate sets."""
    flags = {
        0: [
            "COORDMISS:Menge: coord mismatch",
            "PLAUS:Stk: value 0 below minimum 1",
        ]
    }
    result = _make_transform_result(flags)
    coord_mismatch, _, _, plaus_cells = _index_row_validation_flags(result)
    assert (0, "Menge") in coord_mismatch
    assert (0, "Stk") in plaus_cells
    assert (0, "Menge") not in plaus_cells


def test_empty_flags_yields_empty_plaus_set() -> None:
    result = _make_transform_result({})
    _, _, _, plaus_cells = _index_row_validation_flags(result)
    assert plaus_cells == set()
