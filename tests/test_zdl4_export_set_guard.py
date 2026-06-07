"""Sprint 2 — ZDL-4: the export guard compares position SETS, not just counts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pytest

from src.core.exceptions import ZeroDataLossError
from src.export import excel_exporter
from src.export.excel_exporter import export_to_excel
from src.mapping.schema_registry import load_schema
from src.scoring.audit_trail import BomAuditTrail, CellAudit
from src.scoring.threshold_manager import TrafficLight

_TEMPLATE = excel_exporter._CONFIG_DIR / "target_template.xlsx"


def _audit_with_positions(
    output_positions: list[str], expected_ids: list[str]
) -> BomAuditTrail:
    schema = load_schema()
    field = schema.fields[0]  # position field, column A
    cells = [
        CellAudit(
            row_index=i,
            target_field=field.name,
            target_column=field.column,
            transformed_value=pos,
            classification=TrafficLight.GREEN,
        )
        for i, pos in enumerate(output_positions)
    ]
    return BomAuditTrail(
        source_file="test.pdf",
        customer="K",
        cells=cells,
        expected_position_count=len(expected_ids),
        expected_position_ids=expected_ids,
        guard_basis="position_set",
    )


def _save_spy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    real_wb = openpyxl.load_workbook(_TEMPLATE)
    spy = MagicMock()
    real_wb.save = spy  # type: ignore[method-assign]
    monkeypatch.setattr(excel_exporter.openpyxl, "load_workbook", lambda *a, **k: real_wb)
    return spy


def test_raises_when_identities_differ_despite_matching_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 3 expected, 3 output rows — but identities differ ("3" lost, "4" extra).
    spy = _save_spy(monkeypatch)
    audit = _audit_with_positions(
        output_positions=["1", "2", "4"], expected_ids=["1", "2", "3"]
    )

    with pytest.raises(ZeroDataLossError, match="fehlen im Output"):
        export_to_excel(audit, tmp_path / "out.xlsx", add_audit_sheet=False)

    spy.assert_not_called()


def test_passes_when_all_expected_positions_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spy = _save_spy(monkeypatch)
    audit = _audit_with_positions(
        output_positions=["1", "2", "3"], expected_ids=["1", "2", "3"]
    )

    export_to_excel(audit, tmp_path / "out.xlsx", add_audit_sheet=False)

    spy.assert_called_once()
