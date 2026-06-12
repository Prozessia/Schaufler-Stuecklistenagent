"""B2 acceptance: position reconciler re-injects PDF-only positions as MISSING."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pytest

from src.core.exceptions import ZeroDataLossError
from src.core.models import (
    CellTransformation,
    ExtractionMethod,
    TransformationResult,
    TransformedRow,
)
from src.export import excel_exporter
from src.export.excel_exporter import export_to_excel
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.reconciliation.position_reconciler import reconcile_positions
from src.scoring.audit_trail import BomAuditTrail, CellAudit
from src.scoring.ensemble_scorer import score_bom
from src.scoring.threshold_manager import TrafficLight


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
        source_file="sample.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Pos",
                target_field="Detail Number",
                target_column="A",
                confidence=0.95,
                reasoning="Pos→Detail Number",
                candidate_confidence=0.95,
                candidate_reasoning="Pos→Detail Number",
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


def _result(positions: list[str], *, has_text_layer: bool) -> TransformationResult:
    return TransformationResult(
        source_file="sample.pdf",
        customer="ACME",
        rows=[_extracted_row(i, p) for i, p in enumerate(positions)],
        source_is_pdf=True,
        has_text_layer=has_text_layer,
        extraction_method=(
            ExtractionMethod.PYMUPDF_TEXT
            if has_text_layer
            else ExtractionMethod.GPT4O_VISION
        ),
    )


def test_reconciler_reinjects_missing_text_path() -> None:
    tr = _result(["1-1", "1-2", "1-3"], has_text_layer=True)

    reconcile_positions(tr, ["1-1", "1-2", "1-3", "1-4", "1-5"], _schema())

    assert len(tr.rows) == 5
    synthetic = [r for r in tr.rows if r.is_synthetic]
    assert {r.cells[0].transformed_value for r in synthetic} == {"1-4", "1-5"}
    assert all(
        r.cells[0].method == "synthetic_pdf_only_missing" for r in synthetic
    )
    assert tr.reconciled is True
    assert tr.expected_position_count == 5


def test_reconciler_reinjects_missing_vision_path() -> None:
    # Vision path: 10 positions seen in the RAW rows, but only 8 survived to the
    # extracted rows. has_text_layer=False — the reconciler must still fire.
    tr = _result([str(i) for i in range(1, 9)], has_text_layer=False)
    raw_vision_positions = [str(i) for i in range(1, 11)]

    reconcile_positions(tr, raw_vision_positions, _schema())

    assert len(tr.rows) == 10
    synthetic = [r for r in tr.rows if r.is_synthetic]
    assert {r.cells[0].transformed_value for r in synthetic} == {"9", "10"}
    assert tr.expected_position_count == 10


def test_reconciler_no_phantoms_on_empty_raw() -> None:
    tr = _result(["1-1", "1-2"], has_text_layer=True)

    reconcile_positions(tr, [], _schema())

    assert len(tr.rows) == 2
    assert not any(r.is_synthetic for r in tr.rows)
    assert tr.reconciled is True
    assert tr.expected_position_count == 2


def test_missing_positions_score_as_red() -> None:
    tr = _result(["1-1"], has_text_layer=True)
    reconcile_positions(tr, ["1-1", "1-2"], _schema())

    audit = score_bom(tr, _mapping(), schema=_schema())

    missing_cells = [
        c for c in audit.cells if "RECONCILER_MISSING_POSITION" in c.hard_vetoes
    ]
    assert len(missing_cells) == 1
    assert missing_cells[0].transformed_value == "1-2"
    assert missing_cells[0].classification == TrafficLight.RED
    # No GREEN cell may exist for the synthetic missing position.
    assert all(c.classification != TrafficLight.GREEN for c in missing_cells)


def test_assertion_now_sharp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Reconcile 8 extracted + 10 PDF positions → master-set count = 10.
    tr = _result([str(i) for i in range(1, 9)], has_text_layer=False)
    reconcile_positions(tr, [str(i) for i in range(1, 11)], _schema())
    assert tr.expected_position_count == 10  # master-set, not raw parser count

    # An audit that only carries 8 distinct rows but the master-set count of 10
    # must trip the (now sharp) zero-data-loss guard.
    audit = BomAuditTrail(
        source_file="sample.pdf",
        customer="ACME",
        expected_position_count=tr.expected_position_count,
        cells=[
            CellAudit(
                row_index=i,
                target_field="Detail Number",
                target_column="A",
                transformed_value=str(i + 1),
                classification=TrafficLight.GREEN,
            )
            for i in range(8)
        ],
    )

    real_wb = openpyxl.load_workbook(excel_exporter._CONFIG_DIR / "target_template.xlsx")
    save_spy = MagicMock()
    real_wb.save = save_spy  # type: ignore[method-assign]
    monkeypatch.setattr(
        excel_exporter.openpyxl, "load_workbook", lambda *a, **k: real_wb
    )

    with pytest.raises(ZeroDataLossError, match="DATA LOSS DETECTED"):
        export_to_excel(audit, tmp_path / "out.xlsx", add_audit_sheet=False)
    save_spy.assert_not_called()


# ---------------------------------------------------------------------------
# BUG-011: Unterdeckungs-Synthese für doppelte Positionsnummern
# ---------------------------------------------------------------------------


def test_bug011_duplicate_position_underextraction_synthesises_missing() -> None:
    """BUG-011: counts={"10": 2}, one extracted row → 1 additional synthetic row.

    If the Vision model saw position "10" twice in the RAW rows but only one
    row with that position survived to the extracted set, the reconciler must
    inject exactly one additional synthetic MISSING row so the shortfall is
    visible as RED rather than being silently swallowed.
    """
    tr = _result(["10"], has_text_layer=False)
    reconcile_positions(
        tr,
        raw_pdf_positions=["10"],
        schema=_schema(),
        raw_pdf_position_counts={"10": 2},
    )

    synthetic = [r for r in tr.rows if r.is_synthetic]
    assert len(synthetic) == 1, f"Expected 1 synthetic row, got {len(synthetic)}"
    assert synthetic[0].is_synthetic is True
    assert synthetic[0].cells[0].transformed_value == "10"
    notes = synthetic[0].cells[0].notes
    assert "1 von 2" in notes, f"Expected '1 von 2' in notes, got: {notes!r}"


def test_bug011_no_extra_synthesis_when_fully_extracted() -> None:
    """BUG-011: counts={"10": 2}, two extracted rows → NO additional synthesis.

    When all occurrences are already present, the reconciler must not add
    spurious synthetic rows.
    """
    tr = _result(["10", "10"], has_text_layer=False)
    reconcile_positions(
        tr,
        raw_pdf_positions=["10"],
        schema=_schema(),
        raw_pdf_position_counts={"10": 2},
    )

    synthetic = [r for r in tr.rows if r.is_synthetic]
    assert len(synthetic) == 0, f"Expected no synthetic rows, got {len(synthetic)}"


def test_bug011_empty_counts_leaves_behaviour_unchanged() -> None:
    """BUG-011: counts empty/None → existing behaviour unaffected.

    Existing tests (test_reconciler_reinjects_missing_*) cover the main paths;
    this test verifies that passing an empty or absent counts dict does not
    introduce phantom rows.
    """
    tr = _result(["1-1", "1-2"], has_text_layer=False)
    reconcile_positions(
        tr,
        raw_pdf_positions=["1-1", "1-2"],
        schema=_schema(),
        raw_pdf_position_counts={},
    )

    synthetic = [r for r in tr.rows if r.is_synthetic]
    assert len(synthetic) == 0

    tr2 = _result(["1-1", "1-2"], has_text_layer=False)
    reconcile_positions(
        tr2,
        raw_pdf_positions=["1-1", "1-2"],
        schema=_schema(),
        raw_pdf_position_counts=None,
    )
    synthetic2 = [r for r in tr2.rows if r.is_synthetic]
    assert len(synthetic2) == 0
