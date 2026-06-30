"""Legacy PDF parser — PyMuPDF-based table extraction (no LLM).

Used as a last-resort fallback in structure_normalizer when the primary
Vision/coordinate-reconstruction pipeline fails or no LLM client is available.
Not used in the production happy path.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.core.models import ExtractionMethod, FileFormat, ParsedBOM, SourceMetadata
from src.ingestion.file_router import infer_customer
from src.ingestion.pdf_common import open_pdf_document

logger = logging.getLogger(__name__)

_MIN_COLS = 2


def parse_pdf(filepath: Path | str) -> ParsedBOM:
    """Extract a BOM table from a PDF using PyMuPDF heuristics (no LLM).

    Strategy (in order):
    1. PyMuPDF find_tables() — handles bordered tables well
    2. Block-text column alignment — for borderless / text-layer PDFs
    Returns an empty-row ParsedBOM (not None) on failure so callers can
    always safely inspect .rows.
    """
    filepath = Path(filepath)
    customer = infer_customer(filepath)
    metadata_base = SourceMetadata(
        filename=filepath.name,
        filepath=str(filepath),
        customer=customer,
        format=FileFormat.PDF,
        extraction_method=ExtractionMethod.PYMUPDF_TABLE,
        extraction_confidence=0.0,
    )

    doc = open_pdf_document(filepath)
    try:
        result = _try_find_tables(doc, filepath, metadata_base)
        if result is not None:
            return result
        return _fallback_text_columns(doc, filepath, metadata_base)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Strategy 1: PyMuPDF find_tables
# ---------------------------------------------------------------------------


def _try_find_tables(doc, filepath: Path, base: SourceMetadata) -> ParsedBOM | None:
    all_headers: list[str] = []
    all_rows: list[dict[str, str | None]] = []
    pages_with_data = 0

    for page_num, page in enumerate(doc, start=1):
        try:
            tabs = page.find_tables()
        except Exception:  # noqa: BLE001
            continue
        if not tabs or not tabs.tables:
            continue

        for tab in tabs.tables:
            try:
                data = tab.extract()
            except Exception:  # noqa: BLE001
                continue
            if not data or len(data) < 2:
                continue

            header_row = [str(c or "").strip() for c in data[0]]
            header_row = [h for h in header_row if h]
            if len(header_row) < _MIN_COLS:
                continue

            if not all_headers:
                all_headers = header_row

            for raw_row in data[1:]:
                row_cells = [str(c or "").strip() for c in raw_row]
                if not any(row_cells):
                    continue
                row_dict: dict[str, str | None] = {}
                for i, header in enumerate(all_headers):
                    val = row_cells[i] if i < len(row_cells) else None
                    row_dict[header] = val or None
                all_rows.append(row_dict)

            pages_with_data += 1

    if not all_headers or not all_rows:
        return None

    confidence = min(0.70 + 0.05 * pages_with_data, 0.85)
    return ParsedBOM(
        source=base.model_copy(
            update={
                "extraction_method": ExtractionMethod.PYMUPDF_TABLE,
                "extraction_confidence": confidence,
                "pages": len(doc),
            }
        ),
        headers=all_headers,
        rows=all_rows,
        metadata={"legacy_parser_used": True, "strategy": "find_tables"},
    )


# ---------------------------------------------------------------------------
# Strategy 2: Block-text column alignment
# ---------------------------------------------------------------------------

_WHITESPACE_SEP = re.compile(r"\s{2,}")


def _fallback_text_columns(doc, filepath: Path, base: SourceMetadata) -> ParsedBOM:
    all_lines: list[str] = []
    for page in doc:
        text = page.get_text("text") or ""
        all_lines.extend(line.strip() for line in text.splitlines() if line.strip())

    candidate_header_idx: int | None = None
    candidate_cols: list[str] = []
    for i, line in enumerate(all_lines):
        cols = [c.strip() for c in _WHITESPACE_SEP.split(line) if c.strip()]
        if len(cols) >= _MIN_COLS:
            candidate_header_idx = i
            candidate_cols = cols
            break

    if candidate_header_idx is None or not candidate_cols:
        logger.warning("Legacy parser: no tabular structure detected in %s", filepath.name)
        return ParsedBOM(
            source=base.model_copy(update={"pages": len(doc)}),
            headers=[],
            rows=[],
            metadata={
                "legacy_parser_used": True,
                "strategy": "text_fallback",
                "error": "no_table_detected",
            },
        )

    rows: list[dict[str, str | None]] = []
    for line in all_lines[candidate_header_idx + 1 :]:
        cols = [c.strip() for c in _WHITESPACE_SEP.split(line) if c.strip()]
        if not cols:
            continue
        row_dict: dict[str, str | None] = {}
        for j, header in enumerate(candidate_cols):
            row_dict[header] = cols[j] if j < len(cols) else None
        rows.append(row_dict)

    return ParsedBOM(
        source=base.model_copy(
            update={
                "extraction_method": ExtractionMethod.PYMUPDF_TABLE,
                "extraction_confidence": 0.40,
                "pages": len(doc),
            }
        ),
        headers=candidate_cols,
        rows=rows,
        metadata={"legacy_parser_used": True, "strategy": "text_columns"},
    )
