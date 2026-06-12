"""Core data models for the BOM-Mapper ingestion pipeline."""

from __future__ import annotations

from enum import Enum
import math

from pydantic import BaseModel, Field, field_validator


class FileFormat(str, Enum):
    EXCEL = "excel"
    PDF = "pdf"
    CSV = "csv"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    OPENPYXL = "openpyxl"
    PYMUPDF_TABLE = "pymupdf_table"
    PYMUPDF_TEXT = "pymupdf_text"
    GPT4O_VISION = "gpt4o_vision"
    VISION_FALLBACK = "vision_fallback"
    CSV = "csv"


class SourceMetadata(BaseModel):
    """Metadata about the source file."""

    filename: str
    filepath: str
    customer: str = ""
    format: FileFormat
    language_detected: str = ""
    pages: int | None = None
    extraction_method: ExtractionMethod | None = None
    extraction_confidence: float = 0.0


class ParsedBOM(BaseModel):
    """Unified output from any parser — the single normalized format."""

    source: SourceMetadata
    headers: list[str] = Field(default_factory=list)
    rows: list[dict[str, str | None]] = Field(default_factory=list)
    raw_header_rows: list[list[str | None]] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    # Number of distinct position identifiers the parser detected in the source.
    # Threaded through the pipeline to the exporter for the zero-data-loss guard.
    # 0 means "not determined" → the guard is skipped (see ZeroDataLossError).
    expected_position_count: int = 0
    # B2: every position identifier the parser observed in the source document.
    # Text path: matches of the position pattern in the C1-filtered text layer.
    # Vision path: position-column values from the raw rows BEFORE deduplication.
    # Used by the reconciler as the PDF-side of the master set.
    raw_pdf_positions: list[str] = Field(default_factory=list)
    # RB-1: deterministische räumliche Zeilen-Identität (Koordinaten-Rekonstruktion).
    # row_keys[i] ist die stabile Band-ID von rows[i] — Format "p{page}:b{band:04d}".
    # Unabhängig vom Positionswert: Zeilen mit gleicher Pos-Nr (oder ganz ohne)
    # bleiben distinkt. Leer auf dem Vision/Scan-Pfad — dort bleibt
    # raw_pdf_positions die Quelle des Master-Sets.
    row_keys: list[str] = Field(default_factory=list)
    # RB-1: ALLE Daten-Band-IDs, die der Koordinaten-Pass fand — VOR jedem LLM-Call.
    # Unabhängiger Vollständigkeits-Anker (löst den selbst-referenziellen
    # Positions-Count des Text-Pfads ab, ZDL-1). Das Master-Set wird hieraus
    # gespeist: eine Zeile, die das LLM später kollabiert oder droppt, ist hier
    # trotzdem gezählt → der Guard sieht sie. 5 Teile unter Pos "10" = 5 Bänder
    # (behebt T-007); namenlose Zeilen haben eine Band-ID, aber keine Position.
    pdf_row_bands: list[str] = Field(default_factory=list)
    # B2/BUG-011: Vorkommens-Zähler je normalisierter Position aus den RAW-Vision-Zeilen
    # VOR Deduplizierung. Ermöglicht dem Reconciler, doppelte Positionsnummern (z. B.
    # zweimal "10") zu erkennen und bei Unterdeckung zusätzliche synthetische MISSING-Zeilen
    # zu erzeugen. {} auf dem Text-Pfad — dort zählt das Band-Set.
    raw_pdf_position_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def total_columns(self) -> int:
        return len(self.headers)


class SourceLocation(BaseModel):
    """Approximate location of a source value inside the original document."""

    page: int | None = None
    bbox: list[float] | None = None
    text: str = ""
    match_type: str = ""

    @field_validator("bbox", mode="before")
    @classmethod
    def _normalize_bbox(cls, value: object) -> list[float] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None

        try:
            coords = [float(coord) for coord in value]
        except (TypeError, ValueError):
            return None

        if not all(math.isfinite(coord) for coord in coords):
            return None

        x0, y0, x1, y1 = coords
        if x0 < 0 or y0 < 0 or x1 < x0 or y1 < y0:
            return None

        return coords


# ---------------------------------------------------------------------------
# Phase 4: Value Transformation models
# ---------------------------------------------------------------------------


class CellTransformation(BaseModel):
    """Result of transforming a single cell value."""

    target_field: str
    target_column: str = ""
    source_column: str = ""
    raw_value: str | None = None
    transformed_value: str | None = None
    confidence: float = 0.0
    method: str = ""  # "direct", "regex", "unit_conversion", "master_data", "llm"
    notes: str = ""


class TransformedRow(BaseModel):
    """A single row after value transformation."""

    row_index: int
    cells: list[CellTransformation] = Field(default_factory=list)
    # B2: True when the row was synthesised by the reconciler for a position that
    # exists in the PDF but was missing from the extracted rows (MISSING marker).
    is_synthetic: bool = False
    # RB-1: deterministische Band-ID, aus der diese Zeile extrahiert wurde (oder "").
    # Stabile Zeilen-Identität für den Zero-Data-Loss-Guard — NICHT der
    # Positionswert. Leer auf dem Vision/Scan-Pfad (dort greift der Positions-Pfad).
    source_row_id: str = ""

    def get_cell(self, target_field: str) -> CellTransformation | None:
        for c in self.cells:
            if c.target_field == target_field:
                return c
        return None

    def to_target_dict(self) -> dict[str, str | None]:
        """Return {target_field: transformed_value} dict."""
        return {c.target_field: c.transformed_value for c in self.cells}


class TransformationResult(BaseModel):
    """Complete transformation result for a full BOM."""

    source_file: str = ""
    customer: str = ""
    rows: list[TransformedRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    # Distinct positions detected in the source, carried from ParsedBOM.
    # (B2 overwrites this with the reconciled master-set size.)
    expected_position_count: int = 0
    # B2: the reconciled master set = set(extracted positions) ∪ set(PDF positions).
    expected_position_ids: list[str] = Field(default_factory=list)
    # RB-1: master set der deterministischen Band-IDs =
    #   {row.source_row_id der emittierten Zeilen} ∪ {bom.pdf_row_bands}.
    # Löst expected_position_ids als Guard-Basis auf dem Text-Pfad ab; das
    # Positions-Set bleibt als sekundäres, menschenlesbares Label erhalten.
    # Band-geschlüsselt → 5 Teile unter einer Position sind 5 Einträge (T-007),
    # namenlose Zeilen werden gezählt (Band-ID vorhanden, Position leer).
    expected_row_keys: list[str] = Field(default_factory=list)
    # B2: True once the position reconciler has run on this result.
    reconciled: bool = False
    # ZDL-2: how the zero-data-loss guard threshold was derived, so a missing
    # position anchor never silently disables the guard.
    #   "row_band_set"      — RB-1: master set deterministischer Zeilen-Band-IDs (Text-Pfad)
    #   "position_set"      — master set of detected position identifiers (Vision/Scan)
    #   "row_count_fallback"— no position anchor; distinct extracted row count
    #   "none"              — nothing to guard (no rows / not reconciled)
    guard_basis: str = "none"
    # Per-row validation flags from post-parse plausibility checks.
    # Key = row_index, Value = list of flag strings.
    # Cells mentioned in flags are capped at RED in the scorer.
    row_validation_flags: dict[int, list[str]] = Field(default_factory=dict)
    # Lossless footer/header/note detection. Key = row_index, Value = reason
    # codes (e.g. ["NO_POSITION", "FOOTER_OR_HEADER_TEXT"]). Purely advisory:
    # rows are NEVER dropped and this feeds NO scoring signal — it only lets a
    # reviewer see "this may not be a table row".
    non_data_row_flags: dict[int, list[str]] = Field(default_factory=dict)
    # Whether the source is a PDF (vs. Excel/CSV where values are deterministic).
    source_is_pdf: bool = False
    # The extraction method used for the source document.
    extraction_method: ExtractionMethod | None = None
    # Parser-level extraction confidence from ingestion.
    source_extraction_confidence: float = 0.0
    # Whether the PDF has a usable text layer for ground-truth verification.
    has_text_layer: bool = False
    # Set when Vision parsing failed and legacy PDF parsing was used as fallback.
    vision_fallback_reason: str | None = None
    # Parser notes carried into scoring for audit and confidence adjustments.
    extraction_notes: str = ""
    # Default audit reason for Check 2, optionally refined per cell in scoring.
    check2_reason: str = ""
    # Indicates that the LLM JSON payload needed structural repair before parsing.
    extraction_json_repaired: bool = False
    # Optional per-cell uncertainty markers emitted by the text-path parser.
    extraction_uncertain_cells: dict[int, list[str]] = Field(default_factory=dict)
    # Full digital PDF text layer used for safe document-wide verification fallbacks.
    document_text_layer: str = ""
    # Page-wise digital PDF text used to avoid cross-page row merging in scoring.
    document_text_pages: list[str] = Field(default_factory=list)
    # Approximate source locations keyed by row index and source column.
    source_locations: dict[int, dict[str, SourceLocation]] = Field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def total_cells(self) -> int:
        return sum(len(r.cells) for r in self.rows)

    @property
    def avg_confidence(self) -> float:
        confs = [c.confidence for r in self.rows for c in r.cells]
        return sum(confs) / len(confs) if confs else 0.0
