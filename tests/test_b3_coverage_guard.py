"""B3 acceptance: coverage guard + removal of the legacy PDF-only append."""

from __future__ import annotations

from pathlib import Path

from src.core.models import (
    CellTransformation,
    ExtractionMethod,
    TransformationResult,
    TransformedRow,
)
from src.export.excel_exporter import export_to_excel
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.reconciliation.position_reconciler import reconcile_positions
import src.scoring.ensemble_scorer as es
from src.scoring.ensemble_scorer import score_bom
from src.scoring.threshold_manager import TrafficLight

_VETO = "RECONCILER_MISSING_POSITION"


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


def _extracted_row(idx: int, position: str) -> TransformedRow:
    return TransformedRow(
        row_index=idx,
        cells=[
            CellTransformation(
                target_field="Detail Number",
                target_column="A",
                source_column="Pos",
                raw_value=position,
                transformed_value=position,
                confidence=0.95,
                method="passthrough",
            )
        ],
    )


def _missing_cells(audit) -> list:
    return [c for c in audit.cells if _VETO in c.hard_vetoes]


def test_old_append_function_removed() -> None:
    assert not hasattr(es, "_append_pdf_only_position_audits")


def test_coverage_guard_catches_scorer_gap() -> None:
    # Scorer sees rows for 1-1 and 1-2 only; master set also contains 1-3.
    tr = TransformationResult(
        source_file="x.pdf",
        customer="ACME",
        rows=[_extracted_row(0, "1-1"), _extracted_row(1, "1-2")],
        source_is_pdf=True,
        has_text_layer=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
        reconciled=True,
        expected_position_ids=["1-1", "1-2", "1-3"],
        expected_position_count=3,
    )

    audit = score_bom(tr, _mapping(), schema=_schema())

    missing = _missing_cells(audit)
    assert [c.transformed_value for c in missing] == ["1-3"]
    assert missing[0].classification == TrafficLight.RED
    assert audit.expected_position_count == 3


def test_no_duplicate_missing_entries() -> None:
    # 1-3 already injected by the reconciler as a synthetic MISSING row.
    tr = TransformationResult(
        source_file="x.pdf",
        customer="ACME",
        rows=[_extracted_row(0, "1-1")],
        source_is_pdf=True,
        has_text_layer=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
    )
    reconcile_positions(tr, ["1-1", "1-3"], _schema())  # injects synthetic 1-3

    audit = score_bom(tr, _mapping(), schema=_schema())

    # The guard must NOT add a second 1-3 entry.
    missing_1_3 = [c for c in _missing_cells(audit) if c.transformed_value == "1-3"]
    assert len(missing_1_3) == 1


def test_full_pipeline_no_silent_loss(tmp_path: Path) -> None:
    # 20 positions total; 5 were lost before scoring (3 dedup + 2 truncation).
    # raw_pdf_positions still carries all 20 → reconciler re-injects the 5.
    all_positions = [f"P-{i}" for i in range(1, 21)]
    survived = all_positions[:15]  # 5 lost (P-16..P-20)

    tr = TransformationResult(
        source_file="big.pdf",
        customer="ACME",
        rows=[_extracted_row(i, p) for i, p in enumerate(survived)],
        source_is_pdf=True,
        has_text_layer=False,
        extraction_method=ExtractionMethod.GPT4O_VISION,
    )
    reconcile_positions(tr, all_positions, _schema())

    assert tr.expected_position_count == 20
    assert sum(1 for r in tr.rows if r.is_synthetic) == 5

    audit = score_bom(tr, _mapping(), schema=_schema())

    distinct_rows = {c.row_index for c in audit.cells}
    assert len(distinct_rows) == 20
    assert len(_missing_cells(audit)) == 5

    # Export must succeed (20 rows >= 20 expected) — no ZeroDataLossError.
    out = tmp_path / "out.xlsx"
    result = export_to_excel(audit, out, schema=_schema(), add_audit_sheet=False)
    assert result.exists()
