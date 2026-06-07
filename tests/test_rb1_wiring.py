"""RB-1 wiring (steps 3.1–3.4): row-band identity through reconcile → score → export.

Proves the dormant band-mode path: when the deterministic parser supplies
``row_keys`` / ``pdf_row_bands``, row identity is the spatial band — so N parts
under ONE position number (T-007) survive as N rows, and a dropped band trips the
export guard. The Vision/position path stays on its own branch (covered by
test_reconciler.py / test_zdl4_export_set_guard.py).
"""

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


def _band_row(idx: int, band_id: str, position: str) -> TransformedRow:
    return TransformedRow(
        row_index=idx,
        source_row_id=band_id,
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


def _result(rows: list[TransformedRow]) -> TransformationResult:
    return TransformationResult(
        source_file="sample.pdf",
        customer="ACME",
        rows=rows,
        source_is_pdf=True,
        has_text_layer=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
    )


def test_reconciler_row_band_mode_sets_basis_and_keys() -> None:
    """pdf_row_bands given → master set is band ids, guard_basis=row_band_set."""
    tr = _result([_band_row(0, "p0:b0001", "10"), _band_row(1, "p0:b0002", "10")])

    reconcile_positions(
        tr, [], _schema(), pdf_row_bands=["p0:b0001", "p0:b0002", "p0:b0003"]
    )

    assert tr.guard_basis == "row_band_set"
    assert tr.expected_row_keys == ["p0:b0001", "p0:b0002", "p0:b0003"]
    # The band the parser saw but transform dropped is re-injected as MISSING.
    synthetic = [r for r in tr.rows if r.is_synthetic]
    assert len(synthetic) == 1
    assert synthetic[0].source_row_id == "p0:b0003"
    assert tr.expected_position_count == 3


def test_t007_two_parts_same_position_survive_to_red_free_score() -> None:
    """Two parts share position '10' but have distinct bands → both scored rows."""
    tr = _result([_band_row(0, "p0:b0001", "10"), _band_row(1, "p0:b0002", "10")])
    reconcile_positions(tr, [], _schema(), pdf_row_bands=["p0:b0001", "p0:b0002"])

    audit = score_bom(tr, _mapping(), schema=_schema())

    # Both bands are present → no RECONCILER_MISSING_POSITION veto, two row ids.
    assert not any(
        "RECONCILER_MISSING_POSITION" in c.hard_vetoes for c in audit.cells
    )
    assert {c.source_row_id for c in audit.cells} == {"p0:b0001", "p0:b0002"}
    assert audit.expected_row_keys == ["p0:b0001", "p0:b0002"]
    assert len({c.row_index for c in audit.cells}) == 2


def test_scorer_coverage_guard_injects_red_for_dropped_band() -> None:
    """A master band with no scored cell is injected as RED/MISSING (band keyed)."""
    tr = _result([_band_row(0, "p0:b0001", "10")])
    # Pretend band b0002 was seen by the parser but lost before scoring.
    tr.expected_row_keys = ["p0:b0001", "p0:b0002"]
    tr.guard_basis = "row_band_set"

    audit = score_bom(tr, _mapping(), schema=_schema())

    missing = [
        c for c in audit.cells if "RECONCILER_MISSING_POSITION" in c.hard_vetoes
    ]
    assert len(missing) == 1
    assert missing[0].source_row_id == "p0:b0002"
    assert missing[0].classification == TrafficLight.RED
    assert audit.completeness_guaranteed is True  # row_band_set + text layer


def test_export_band_guard_raises_on_dropped_band(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Export must refuse to save when an expected band id is absent from output."""
    audit = BomAuditTrail(
        source_file="sample.pdf",
        customer="ACME",
        expected_row_keys=["p0:b0001", "p0:b0002", "p0:b0003"],
        guard_basis="row_band_set",
        cells=[
            CellAudit(
                row_index=i,
                source_row_id=band,
                target_field="Detail Number",
                target_column="A",
                transformed_value="10",
                classification=TrafficLight.GREEN,
            )
            for i, band in enumerate(["p0:b0001", "p0:b0002"])  # b0003 missing
        ],
    )

    real_wb = openpyxl.load_workbook(excel_exporter._CONFIG_DIR / "target_template.xlsx")
    save_spy = MagicMock()
    real_wb.save = save_spy  # type: ignore[method-assign]
    monkeypatch.setattr(
        excel_exporter.openpyxl, "load_workbook", lambda *a, **k: real_wb
    )

    with pytest.raises(ZeroDataLossError, match="Zeilen-Bändern fehlen"):
        export_to_excel(audit, tmp_path / "out.xlsx", add_audit_sheet=False)
    save_spy.assert_not_called()
