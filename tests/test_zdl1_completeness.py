"""Sprint 2 — ZDL-1: the completeness verdict must be honest. Scanned/Vision-only
PDFs (no text layer) are flagged 'not guaranteed', not silently treated as safe."""

from __future__ import annotations

from src.core.models import (
    CellTransformation,
    ExtractionMethod,
    TransformationResult,
    TransformedRow,
)
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.reconciliation.position_reconciler import reconcile_positions
from src.scoring.ensemble_scorer import score_bom


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            TargetField(
                name="Detail Number",
                name_de="Positionsnummer",
                column="A",
                type="string",
                required=True,
            )
        ]
    )


def _mapping() -> MappingResult:
    return MappingResult(
        source_file="x.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Pos",
                target_field="Detail Number",
                target_column="A",
                confidence=0.95,
                reasoning="x",
                candidate_confidence=0.95,
                candidate_reasoning="x",
            )
        ],
    )


def _row(idx: int, pos: str) -> TransformedRow:
    return TransformedRow(
        row_index=idx,
        cells=[
            CellTransformation(
                target_field="Detail Number",
                target_column="A",
                source_column="Pos",
                raw_value=pos,
                transformed_value=pos,
                confidence=0.95,
                method="passthrough",
            )
        ],
    )


def test_vision_only_pdf_is_not_completeness_guaranteed() -> None:
    tr = TransformationResult(
        source_file="scan.pdf",
        customer="ACME",
        rows=[_row(0, "1"), _row(1, "2")],
        source_is_pdf=True,
        has_text_layer=False,  # scanned / Vision-only
        extraction_method=ExtractionMethod.GPT4O_VISION,
    )
    reconcile_positions(tr, ["1", "2"], _schema())

    audit = score_bom(tr, _mapping(), schema=_schema())

    assert audit.completeness_guaranteed is False
    assert "Text-Layer" in audit.completeness_reason


def test_text_layer_pdf_is_completeness_guaranteed() -> None:
    tr = TransformationResult(
        source_file="digital.pdf",
        customer="ACME",
        rows=[_row(0, "1"), _row(1, "2")],
        source_is_pdf=True,
        has_text_layer=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
    )
    reconcile_positions(tr, ["1", "2"], _schema())

    audit = score_bom(tr, _mapping(), schema=_schema())

    assert audit.completeness_guaranteed is True


def test_no_position_anchor_is_not_guaranteed() -> None:
    tr = TransformationResult(
        source_file="digital.pdf",
        customer="ACME",
        rows=[_row(0, "1")],
        source_is_pdf=True,
        has_text_layer=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
    )
    # No raw positions and the single row carries one → master set non-empty here,
    # so force the fallback by clearing positions instead:
    tr.rows = [
        TransformedRow(
            row_index=0,
            cells=[
                CellTransformation(
                    target_field="Detail Number",
                    target_column="A",
                    transformed_value=None,
                    method="passthrough",
                )
            ],
        )
    ]
    reconcile_positions(tr, [], _schema())  # guard_basis -> row_count_fallback/none

    audit = score_bom(tr, _mapping(), schema=_schema())

    assert audit.completeness_guaranteed is False
    assert audit.guard_basis != "position_set"
