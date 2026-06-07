"""Helpers for applying manual cell edits to persisted audit trails."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from src.api.models.schemas import CellEditRequest
from src.scoring.audit_trail import BomAuditTrail, CellAudit, RowExclusion
from src.scoring.threshold_manager import TrafficLight


def recalculate_audit_summary(audit: BomAuditTrail) -> None:
    """Recompute audit summary counts after manual overrides.

    Cells belonging to reviewer-excluded rows (R3) are not counted — an excluded
    row is no longer part of the result.
    """
    counts = {
        TrafficLight.GREEN: 0,
        TrafficLight.YELLOW: 0,
        TrafficLight.RED: 0,
        TrafficLight.NEUTRAL: 0,
        TrafficLight.MANUAL_CONFIRMED: 0,
    }

    excluded = set(audit.excluded_rows or [])
    for cell in audit.cells:
        if cell.row_index in excluded:
            continue
        counts[cell.classification] += 1

    audit.green_count = counts[TrafficLight.GREEN]
    audit.yellow_count = counts[TrafficLight.YELLOW]
    audit.red_count = counts[TrafficLight.RED]
    audit.neutral_count = counts[TrafficLight.NEUTRAL]
    audit.manual_confirmed_count = counts[TrafficLight.MANUAL_CONFIRMED]
    audit.total_scored = (
        audit.green_count
        + audit.yellow_count
        + audit.red_count
        + audit.manual_confirmed_count
    )


def apply_cell_edits(audit: BomAuditTrail, edits: Sequence[CellEditRequest]) -> None:
    """Apply browser edits directly to the export-relevant audit model."""
    lookup = {(cell.row_index, cell.target_field): cell for cell in audit.cells}
    target_columns = {}
    for cell in audit.cells:
        target_columns.setdefault(cell.target_field, cell.target_column)

    for edit in edits:
        key = (edit.row_index, edit.target_field)
        cell = lookup.get(key)
        if cell is None:
            cell = CellAudit(
                row_index=edit.row_index,
                target_field=edit.target_field,
                target_column=target_columns.get(edit.target_field, ""),
                raw_value=None,
                transformed_value=None,
                classification=TrafficLight.NEUTRAL,
            )
            audit.cells.append(cell)
            lookup[key] = cell

        previous_value = (
            cell.transformed_value
            if cell.transformed_value is not None
            else cell.raw_value
        )

        cell.transformed_value = edit.corrected_value
        cell.transform_method = "manual_override"
        cell.transform_confidence = 1.0
        cell.rule_score = max(cell.rule_score, 0.95)
        cell.context_score = max(cell.context_score, 0.95)
        cell.soft_score = max(cell.soft_score, 0.95)
        cell.verify_score = max(cell.verify_score, 0.0)
        cell.final_score = 1.0
        cell.promotion_reason = "manual_override"
        cell.decision_contract_version = "manual_confirmed_v1"
        cell.classification = TrafficLight.MANUAL_CONFIRMED
        cell.final_status = TrafficLight.MANUAL_CONFIRMED.value
        cell.manual_edit_reason = "manual user correction"
        cell.green_evidence = []
        cell.reasoning = (
            "Manual confirmation via browser grid"
            if previous_value in (None, "")
            else f"Manual correction via browser grid. Previous value: {previous_value}"
        )

    recalculate_audit_summary(audit)


def apply_row_exclusions(
    audit: BomAuditTrail,
    row_indices: Sequence[int],
    *,
    excluded: bool,
    reason: str = "excluded by user",
) -> list[int]:
    """Mark rows as deliberately excluded (or restore them) in the audit trail.

    Excluding a row is an explicit, audited reviewer action: the row vanishes from
    the export and the counts, and the exporter's zero-data-loss guard treats its
    band as legitimately removed (no silent loss). Returns the row indices whose
    state actually changed.

    Args:
        audit:       The persisted audit trail to mutate.
        row_indices: Row indices to (un)exclude.
        excluded:    True to exclude, False to restore.
        reason:      Human-readable reason recorded in the exclusion log.
    """
    known_rows = {cell.row_index for cell in audit.cells}
    current = set(audit.excluded_rows or [])
    source_row_id_by_row: dict[int, str] = {}
    for cell in audit.cells:
        if cell.source_row_id and cell.row_index not in source_row_id_by_row:
            source_row_id_by_row[cell.row_index] = cell.source_row_id

    changed: list[int] = []
    for row_index in row_indices:
        if row_index not in known_rows:
            continue
        if excluded and row_index not in current:
            current.add(row_index)
            audit.exclusion_log.append(
                RowExclusion(
                    row_index=row_index,
                    source_row_id=source_row_id_by_row.get(row_index, ""),
                    reason=reason,
                    excluded_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            changed.append(row_index)
        elif not excluded and row_index in current:
            current.discard(row_index)
            audit.exclusion_log = [
                entry
                for entry in audit.exclusion_log
                if entry.row_index != row_index
            ]
            changed.append(row_index)

    audit.excluded_rows = sorted(current)
    recalculate_audit_summary(audit)
    return changed
