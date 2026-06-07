"""A2 acceptance: zero-data-loss guard in excel_exporter.export_to_excel."""

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


def _make_audit(*, distinct_rows: int, expected_position_count: int) -> BomAuditTrail:
    """Build an audit with `distinct_rows` distinct row indices."""
    schema = load_schema()
    field = schema.fields[0]  # "Detail Number" (column A)
    cells = [
        CellAudit(
            row_index=i,
            target_field=field.name,
            target_column=field.column,
            transformed_value=str(i + 1),
            classification=TrafficLight.GREEN,
        )
        for i in range(distinct_rows)
    ]
    return BomAuditTrail(
        source_file="test.pdf",
        customer="TestKunde",
        cells=cells,
        expected_position_count=expected_position_count,
    )


def _patch_workbook_with_save_spy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Load a real workbook but replace .save with a spy (no file written)."""
    real_wb = openpyxl.load_workbook(_TEMPLATE)
    save_spy = MagicMock()
    real_wb.save = save_spy  # type: ignore[method-assign]
    monkeypatch.setattr(
        excel_exporter.openpyxl, "load_workbook", lambda *a, **k: real_wb
    )
    return save_spy


def test_zero_data_loss_assertion_raises_and_does_not_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """expected=100, actual=80 → ZeroDataLossError, wb.save() NOT called."""
    save_spy = _patch_workbook_with_save_spy(monkeypatch)
    audit = _make_audit(distinct_rows=80, expected_position_count=100)
    out = tmp_path / "out.xlsx"

    with pytest.raises(ZeroDataLossError, match="DATA LOSS DETECTED"):
        export_to_excel(audit, out, add_audit_sheet=False)

    save_spy.assert_not_called()
    assert not out.exists()


def test_zero_data_loss_assertion_passes_and_saves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """expected=100, actual=100 → no error, wb.save() called once."""
    save_spy = _patch_workbook_with_save_spy(monkeypatch)
    audit = _make_audit(distinct_rows=100, expected_position_count=100)
    out = tmp_path / "out.xlsx"

    result = export_to_excel(audit, out, add_audit_sheet=False)

    assert result == out
    save_spy.assert_called_once()


def test_zero_data_loss_guard_skipped_when_count_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """expected=0 (not set) → guard skipped, export proceeds (no regression)."""
    save_spy = _patch_workbook_with_save_spy(monkeypatch)
    audit = _make_audit(distinct_rows=5, expected_position_count=0)
    out = tmp_path / "out.xlsx"

    export_to_excel(audit, out, add_audit_sheet=False)

    save_spy.assert_called_once()
