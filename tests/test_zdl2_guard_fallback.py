"""Sprint 1 — ZDL-2: the zero-data-loss guard must not silently disable itself
when no position anchor can be inferred."""

from __future__ import annotations

from src.core.models import (
    CellTransformation,
    ExtractionMethod,
    TransformationResult,
    TransformedRow,
)
from src.mapping.schema_registry import TargetField, TargetSchema
from src.reconciliation.position_reconciler import reconcile_positions


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            TargetField(
                name="Detail Number",
                name_de="Positionsnummer",
                column="A",
                type="string",
                required=True,
            ),
            TargetField(
                name="Benennung",
                name_de="Benennung",
                column="C",
                type="string",
                required=True,
            ),
        ]
    )


def _row_without_position(idx: int, name: str) -> TransformedRow:
    """A row that carries a description but NO position value."""
    return TransformedRow(
        row_index=idx,
        cells=[
            CellTransformation(
                target_field="Benennung",
                target_column="C",
                source_column="Bez",
                raw_value=name,
                transformed_value=name,
                confidence=0.8,
                method="passthrough",
            )
        ],
    )


def test_guard_not_silently_skipped_without_position_column() -> None:
    # No position cells, no raw PDF positions → master set is empty.
    tr = TransformationResult(
        source_file="no_pos.pdf",
        customer="ACME",
        rows=[_row_without_position(i, f"Teil {i}") for i in range(3)],
        source_is_pdf=True,
        extraction_method=ExtractionMethod.GPT4O_VISION,
    )

    reconcile_positions(tr, raw_pdf_positions=[], schema=_schema())

    # Guard must be ARMED via the row-count fallback, not disabled.
    assert tr.guard_basis == "row_count_fallback"
    assert tr.expected_position_count == 3
    assert tr.reconciled is True
    # No synthetic rows were added (no positions to re-inject).
    assert not any(r.is_synthetic for r in tr.rows)


def test_position_set_basis_when_positions_present() -> None:
    tr = TransformationResult(
        source_file="pos.pdf",
        customer="ACME",
        rows=[
            TransformedRow(
                row_index=0,
                cells=[
                    CellTransformation(
                        target_field="Detail Number",
                        target_column="A",
                        transformed_value="1-1",
                        confidence=0.9,
                        method="passthrough",
                    )
                ],
            )
        ],
        source_is_pdf=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
    )

    reconcile_positions(tr, raw_pdf_positions=["1-1", "1-2"], schema=_schema())

    assert tr.guard_basis == "position_set"
    assert tr.expected_position_count == 2


def test_empty_result_has_no_guard_basis() -> None:
    tr = TransformationResult(source_file="empty.pdf", customer="ACME", rows=[])
    reconcile_positions(tr, raw_pdf_positions=[], schema=_schema())
    assert tr.guard_basis == "none"
    assert tr.expected_position_count == 0
