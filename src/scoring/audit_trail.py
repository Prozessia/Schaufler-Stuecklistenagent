"""Audit Trail — per-cell decision documentation for traceability.

Records every scoring signal, applied rules, and final classification
so that any decision can be traced back and explained.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.models import SourceLocation
from src.scoring.threshold_manager import TrafficLight

logger = logging.getLogger(__name__)


class CellAudit(BaseModel):
    """Full audit record for a single cell's scoring decision."""

    row_index: int
    # RB-1: deterministische Band-ID dieser Zelle (Zero-Data-Loss-Guard-Schlüssel).
    # Aus TransformedRow.source_row_id durchgereicht; "" auf dem Vision/Scan-Pfad.
    source_row_id: str = ""
    target_field: str
    target_column: str = ""
    source_column: str = ""

    # Raw and transformed values
    raw_value: str | None = None
    transformed_value: str | None = None
    transform_method: str = ""
    source_location: SourceLocation | None = None

    # Signal 1: Transform confidence (from Phase 4)
    transform_confidence: float = 0.0

    # Mapping confidence from Phase 3
    mapping_confidence: float = 0.0

    # Triple-check metadata (ZERO FALSE POSITIVE contract)
    candidate_confidence: float = 0.0
    candidate_reasoning: str = ""
    pdf_extracted_value: str | None = None
    pdf_extraction_confidence: float = 0.0
    check2_reason: str = ""
    pdf_source_location: str = ""
    value_match_result: str = "uncertain"
    value_match_detail: str = ""
    blocking_errors: list[str] = Field(default_factory=list)
    field_category: str = ""
    green_evidence: list[str] = Field(default_factory=list)
    final_status: str = "red"
    manual_edit_reason: str = ""

    # Signal 2: Rule-based validation score (computed in scorer)
    rule_score: float = 0.0
    rule_details: list[str] = Field(default_factory=list)

    # Signal 3: Counter-check (optional)
    counter_check_score: float | None = None
    counter_check_notes: str = ""

    # Signal 4: Contextual consistency signal
    context_score: float = 0.0

    # Verification signals
    spatial_score: float = 0.0
    anchor_score: float = 0.0
    master_score: float = 0.0

    # Composite signals (masterplan contract)
    soft_score: float = 0.0
    verify_score: float = 0.0

    soft_breakdown: dict[str, float] = Field(default_factory=dict)
    verify_breakdown: dict[str, float] = Field(default_factory=dict)

    # Contract metadata
    hard_vetoes: list[str] = Field(default_factory=list)
    is_neutral_empty: bool = False
    promotion_reason: str = ""
    decision_contract_version: str = "v2_verify_contract"

    # Final composite
    final_score: float = 0.0
    classification: TrafficLight = TrafficLight.RED
    reasoning: str = ""


class MappingValidationFinding(BaseModel):
    """A finding from the rule-based mapping validator (Phase 3 middleware)."""

    severity: str  # "error" | "warning" | "info"
    message: str
    source_column: str = ""
    target_field: str = ""


class RowExclusion(BaseModel):
    """Provenance record for a reviewer-excluded row (R3)."""

    row_index: int
    source_row_id: str = ""
    reason: str = "excluded by user"
    excluded_at: str = ""  # ISO-8601 UTC timestamp


class BomAuditTrail(BaseModel):
    """Complete audit trail for one BOM file."""

    source_file: str = ""
    customer: str = ""
    cells: list[CellAudit] = Field(default_factory=list)
    mapping_validation_issues: list[MappingValidationFinding] = Field(
        default_factory=list
    )
    # Distinct positions expected in the output (from parser/reconciler).
    # Used by the exporter's zero-data-loss guard; 0 = guard skipped.
    expected_position_count: int = 0
    # ZDL-4: the reconciled master set of position identifiers, carried so the
    # exporter can assert the output covers the exact set (not just the count).
    expected_position_ids: list[str] = Field(default_factory=list)
    # RB-1: master set der deterministischen Zeilen-Band-IDs (Text-Pfad). Vom
    # Scorer gestempelt, vom Exporter als Set assertiert. Leer auf dem
    # Vision/Scan-Pfad, dort bleibt expected_position_ids die Guard-Basis.
    expected_row_keys: list[str] = Field(default_factory=list)
    # ZDL-2: how expected_position_count was derived (see TransformationResult).
    # Surfaced so a missing position anchor is visible, not a silent skip.
    guard_basis: str = "none"
    # ZDL-1: True only when the position set rests on an INDEPENDENT ground truth
    # (digital text layer). False for scanned/Vision-only PDFs, legacy fallback,
    # or when no position anchor exists — i.e. completeness is NOT guaranteed.
    completeness_guaranteed: bool = False
    # Human-readable reason for the completeness verdict (shown in the dashboard).
    completeness_reason: str = ""

    # R3: rows the reviewer has DELIBERATELY excluded (junk/footer rows that slipped
    # through). Keyed by row_index. This is an explicit, audited user action — it is
    # NOT silent data loss, so the exporter's zero-data-loss guard treats these row
    # bands as legitimately removed (their keys are dropped from the expected set).
    excluded_rows: list[int] = Field(default_factory=list)
    # Per-excluded-row provenance (reason + ISO timestamp) for the audit trail.
    exclusion_log: list[RowExclusion] = Field(default_factory=list)
    # Lossless footer/header/note detection (advisory). Key = row_index, Value =
    # reason codes. These rows are NOT removed and NOT scored differently — the
    # flag only lets a reviewer see "this may not be a real table row".
    non_data_row_flags: dict[int, list[str]] = Field(default_factory=dict)

    # Summary stats
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    neutral_count: int = 0
    manual_confirmed_count: int = 0
    total_scored: int = 0

    @property
    def total_cells(self) -> int:
        return len(self.cells)

    @property
    def green_pct(self) -> float:
        return self.green_count / self.total_scored * 100 if self.total_scored else 0.0

    @property
    def yellow_pct(self) -> float:
        return self.yellow_count / self.total_scored * 100 if self.total_scored else 0.0

    @property
    def red_pct(self) -> float:
        return self.red_count / self.total_scored * 100 if self.total_scored else 0.0

    @property
    def neutral_pct(self) -> float:
        return self.neutral_count / self.total_cells * 100 if self.total_cells else 0.0

    @property
    def manual_confirmed_pct(self) -> float:
        return (
            self.manual_confirmed_count / self.total_scored * 100
            if self.total_scored
            else 0.0
        )

    def get_cells_by_classification(self, cls: TrafficLight) -> list[CellAudit]:
        return [c for c in self.cells if c.classification == cls]

    def get_row_cells(self, row_index: int) -> list[CellAudit]:
        return [c for c in self.cells if c.row_index == row_index]

    def export_json(self, path: Path) -> None:
        """Export audit trail as JSON file."""
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Audit trail exported to %s", path)
