"""API response and request models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.core.models import SourceLocation


class JobStatus(BaseModel):
    """Status of a processing job."""

    job_id: str
    status: str  # "pending", "processing", "completed", "failed"
    filename: str = ""
    customer: str = ""
    progress: float = 0.0  # 0.0 - 1.0
    error: str | None = None


class JobSummary(JobStatus):
    """List-view projection of a job — JobStatus plus the denormalized counters
    (Phase 0), served without parsing the audit blob."""

    created_at: float = 0.0
    updated_at: float = 0.0
    total_rows: int = 0
    total_cells: int = 0
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    neutral_count: int = 0
    manual_confirmed_count: int = 0
    completeness_guaranteed: bool = False
    expected_position_count: int = 0
    archived: bool = False


class CellResult(BaseModel):
    """A single cell in the result table."""

    row_index: int
    target_field: str
    target_column: str = ""
    raw_value: str | None = None
    transformed_value: str | None = None
    method: str = ""
    score: float = 0.0
    classification: str = (
        "red"  # "green", "yellow", "red", "neutral", "manual_confirmed"
    )
    final_status: str = "red"
    value_match_result: str = "uncertain"
    value_match_detail: str = ""
    field_category: str = ""
    green_evidence: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    hard_vetoes: list[str] = Field(default_factory=list)
    reasoning: str = ""
    source_location: SourceLocation | None = None


class TemplateColumnResult(BaseModel):
    """Excel-template metadata for one visible result column."""

    field: str
    column: str = ""
    header_label: str = ""
    header_lines: list[str] = Field(default_factory=list)
    width: float | None = None
    type: str = "string"
    required: bool = False
    horizontal_alignment: str | None = None
    vertical_alignment: str | None = None


class TemplateMetaSectionResult(BaseModel):
    """Merged worksheet section above the table header."""

    key: str
    label: str = ""
    value: str = ""
    start_column: str = ""
    end_column: str = ""
    label_row: int = 0
    value_row: int = 0
    label_horizontal_alignment: str | None = None
    label_vertical_alignment: str | None = None
    value_horizontal_alignment: str | None = None
    value_vertical_alignment: str | None = None


class TemplateDefaultCellResult(BaseModel):
    """Default value cell rendered between header and data rows."""

    field: str
    column: str = ""
    value: str = ""
    horizontal_alignment: str | None = None
    vertical_alignment: str | None = None


class TemplateLayoutResult(BaseModel):
    """Workbook layout metadata used to render an Excel-like grid."""

    title: str = ""
    sheet_name: str = ""
    header_row: int = 0
    data_start_row: int = 0
    freeze_panes: str | None = None
    header_height: float | None = None
    data_row_height: float | None = None
    title_row: int = 1
    spacer_row: int = 4
    default_row: int = 6
    default_row_height: float | None = None
    meta_sections: list[TemplateMetaSectionResult] = Field(default_factory=list)
    default_cells: list[TemplateDefaultCellResult] = Field(default_factory=list)


class RowResult(BaseModel):
    """One BOM row with all cells."""

    row_index: int
    cells: list[CellResult] = Field(default_factory=list)
    worst_classification: str = "red"
    # Lossless footer/header/note detection (advisory). When True, this row looks
    # like a page footer/header or free-text note rather than a real BOM position.
    # The row is NOT dropped and NOT scored differently — the UI can badge it.
    non_data: bool = False
    non_data_reasons: list[str] = Field(default_factory=list)


class JobResult(BaseModel):
    """Full result for a completed job."""

    job_id: str
    filename: str
    customer: str
    total_rows: int = 0
    total_cells: int = 0
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    neutral_count: int = 0
    manual_confirmed_count: int = 0
    green_pct: float = 0.0
    yellow_pct: float = 0.0
    red_pct: float = 0.0
    neutral_pct: float = 0.0
    manual_confirmed_pct: float = 0.0
    # ZDL-1: completeness guarantee surfaced for the dashboard banner.
    completeness_guaranteed: bool = False
    completeness_reason: str = ""
    expected_position_count: int = 0
    guard_basis: str = "none"
    # R3: row indices the reviewer deliberately excluded (hidden from rows, skipped
    # on export). Surfaced so the UI can show a "N ausgeschlossen — wiederherstellen".
    excluded_rows: list[int] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    columns: list[TemplateColumnResult] = Field(default_factory=list)
    template: TemplateLayoutResult = Field(default_factory=TemplateLayoutResult)
    rows: list[RowResult] = Field(default_factory=list)


# Rebuild model after RowResult is defined
JobResult.model_rebuild()


class FeedbackRequest(BaseModel):
    """A correction submitted by the user."""

    row_index: int
    target_field: str
    corrected_value: str
    correction_type: str = "value"  # "value", "mapping", "rejected"


class CellEditRequest(BaseModel):
    """A direct cell edit from the browser grid."""

    row_index: int
    target_field: str
    corrected_value: str


class RowExclusionRequest(BaseModel):
    """Reviewer request to exclude (or restore) rows from a job result (R3)."""

    row_indices: list[int]
    excluded: bool = True
    reason: str = "excluded by user"


class UploadResponse(BaseModel):
    """Response after uploading a file."""

    job_id: str
    filename: str
    message: str = "Processing started"
