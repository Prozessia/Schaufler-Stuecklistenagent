"""PDF Parser — extract tables from PDF files into ParsedBOM.

Strategy (from Phase 1 analysis):
1. PyMuPDF find_tables() — works for most PDFs
2. PyMuPDF text extraction + line-based parsing — fallback for fragmented tables
3. Vision fallback (GPT-4o) — future, for GF and TCG/Unitech CAD frames
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

from src.core.models import (
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceMetadata,
)
from src.ingestion.file_router import infer_customer
from src.ingestion.pdf_common import open_pdf_document

logger = logging.getLogger(__name__)

# Minimum columns for a table to be considered a valid BOM table
_MIN_COLS = 3
# Minimum data rows (excluding header)
_MIN_DATA_ROWS = 2


def parse_pdf(filepath: Path | str) -> ParsedBOM:
    """Parse a PDF file. Tries table extraction first, falls back to text parsing."""
    filepath = Path(filepath)
    doc = open_pdf_document(filepath)

    page_count = len(doc)

    # Strategy 1: PyMuPDF table extraction
    result = _try_table_extraction(doc, filepath, page_count)
    if result is not None and _extraction_quality_ok(result):
        doc.close()
        return result

    # Strategy 1.5: Word-position-based extraction (precise x-coordinate alignment)
    result = _try_word_position_extraction(doc, filepath, page_count)
    if result is not None:
        doc.close()
        return result

    # Strategy 2: Text-based line parsing
    result = _try_text_extraction(doc, filepath, page_count)
    if result is not None:
        doc.close()
        return result

    # Nothing worked — return empty with low confidence
    doc.close()
    logger.warning("No tables found in %s — needs Vision fallback", filepath.name)
    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=page_count,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.0,
        ),
        metadata={"warning": "No tables extracted. Vision fallback recommended."},
    )


# ---------------------------------------------------------------------------
# Strategy 1: PyMuPDF find_tables()
# ---------------------------------------------------------------------------


def _try_table_extraction(
    doc: fitz.Document, filepath: Path, page_count: int
) -> ParsedBOM | None:
    """Extract tables using PyMuPDF's built-in table finder."""
    all_tables: list[list[list[str | None]]] = []
    main_col_count = 0

    for page in doc:
        try:
            finder = page.find_tables()
        except Exception:
            logger.debug(
                "find_tables() crashed on page %d of %s", page.number, filepath.name
            )
            continue
        tables = finder.tables if hasattr(finder, "tables") else []

        for table in tables:
            try:
                extracted = table.extract()
            except Exception:
                continue

            if not extracted or len(extracted) < 1:
                continue

            col_count = max(len(r) for r in extracted)
            if col_count < _MIN_COLS:
                continue

            # Track the widest table's column count
            if col_count > main_col_count:
                main_col_count = col_count

            all_tables.append(extracted)

    if not all_tables:
        return None

    # Merge tables across pages that share the same structure,
    # then detect the real header row (not necessarily row 0).
    headers, rows, header_idx = _merge_tables_with_header_detection(
        all_tables, main_col_count
    )

    if headers is None or len(rows) < _MIN_DATA_ROWS:
        return None

    # Clean headers
    clean_headers = _clean_header_row(headers, main_col_count)

    # Build row dicts
    row_dicts = _rows_to_dicts(rows, clean_headers)

    confidence = _estimate_confidence(len(row_dicts), page_count, main_col_count)

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=page_count,
            extraction_method=ExtractionMethod.PYMUPDF_TABLE,
            extraction_confidence=confidence,
        ),
        headers=clean_headers,
        rows=row_dicts,
        raw_header_rows=[headers] if headers else [],
        metadata={
            "tables_found": len(all_tables),
            "main_col_count": main_col_count,
            "detected_header_row": header_idx,
        },
    )


# ---------------------------------------------------------------------------
# Quality gate for find_tables() output
# ---------------------------------------------------------------------------


def _extraction_quality_ok(bom: ParsedBOM) -> bool:
    """Check whether the table-extraction result looks reasonable.

    Rejects tables where:
    - Most headers are auto-generated placeholders (_col_N)
    - Header cells contain very long text (= data bled into header)
    - Very few recognised BOM keywords in headers
    """
    if not bom.headers:
        return False

    placeholder_count = sum(1 for h in bom.headers if re.match(r"^_col_\d+$", h))
    placeholder_ratio = placeholder_count / len(bom.headers)

    # Count recognised keywords in cleaned headers
    keyword_hits = 0
    long_header_count = 0
    for h in bom.headers:
        h_lower = h.lower()
        for kw in _HEADER_KEYWORDS:
            if kw in h_lower:
                keyword_hits += 1
                break
        if len(h) > 60:
            long_header_count += 1

    keyword_ratio = keyword_hits / len(bom.headers)

    # Reject if >40% placeholder headers AND few BOM keywords
    if placeholder_ratio > 0.40 and keyword_ratio < 0.3:
        logger.info(
            "find_tables() quality LOW for %s: placeholders=%.0f%%, keywords=%.0f%% -> fallback",
            bom.source.filename,
            placeholder_ratio * 100,
            keyword_ratio * 100,
        )
        return False

    # Reject if headers contain very long strings (data bleeding)
    if long_header_count > len(bom.headers) * 0.3:
        logger.info(
            "find_tables() quality LOW for %s: %d/%d headers >60 chars -> fallback",
            bom.source.filename,
            long_header_count,
            len(bom.headers),
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Strategy 1.5: Word-position-based extraction
# ---------------------------------------------------------------------------


def _try_word_position_extraction(
    doc: fitz.Document, filepath: Path, page_count: int
) -> ParsedBOM | None:
    """Extract table data using word x/y coordinates from all pages.

    This handles PDFs where find_tables() fails due to CAD frames or
    misdetected grid lines. It uses the x-positions of header keywords
    to define column boundaries, then assigns each data word to the
    correct column based on horizontal overlap.
    """
    # 1. Collect words from all pages with coordinates
    all_words: list[tuple[float, float, float, float, str, int]] = (
        []
    )  # x0,y0,x1,y1,text,page
    for page_num, page in enumerate(doc):
        for w in page.get_text("words"):
            # w = (x0, y0, x1, y1, "text", block_no, line_no, word_no)
            all_words.append((w[0], w[1], w[2], w[3], w[4], page_num))

    if not all_words:
        return None

    # 2. Find the header row: scan for a y-level with multiple BOM-keyword hits
    header_info = _find_header_by_word_positions(all_words)
    if header_info is None:
        return None

    header_y, columns = header_info
    # columns = [(x_start, x_end, column_name), ...]
    if len(columns) < _MIN_COLS:
        return None

    logger.info(
        "Word-position extraction for %s: found %d columns at y=%.1f",
        filepath.name,
        len(columns),
        header_y,
    )

    # 3. Extract data rows by grouping words below the header into y-bands
    headers = [c[2] for c in columns]
    rows = _extract_rows_by_word_positions(all_words, header_y, columns, page_count)

    if len(rows) < _MIN_DATA_ROWS:
        return None

    row_dicts = _rows_to_dicts(rows, headers)

    # 4. Post-processing: merge continuation rows & filter page-number rows
    row_dicts = _postprocess_word_position_rows(row_dicts, headers)

    confidence = min(
        0.85, _estimate_confidence(len(row_dicts), page_count, len(columns))
    )

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=page_count,
            extraction_method=ExtractionMethod.PYMUPDF_TABLE,
            extraction_confidence=confidence,
        ),
        headers=headers,
        rows=row_dicts,
        metadata={
            "parsing_mode": "word_position_based",
            "header_y": round(header_y, 1),
            "columns_detected": len(columns),
        },
    )


def _postprocess_word_position_rows(
    row_dicts: list[dict[str, str | None]],
    headers: list[str],
) -> list[dict[str, str | None]]:
    """Clean up rows from word-position extraction.

    1. Remove page-number rows (rows where most values are single digits matching
       column indices like "1", "2", "3"...).
    2. Merge continuation rows (rows without a Position/POS value) into the
       preceding data row by appending their non-empty cells.
    """
    # Detect which header is the "position" column
    pos_header = None
    for h in headers:
        if h.lower() in ("pos", "position", "nr", "no", "num", "detail"):
            pos_header = h
            break

    cleaned: list[dict[str, str | None]] = []

    for row in row_dicts:
        non_empty = {k: v for k, v in row.items() if v}

        # Filter page-number rows: rows where all/most values are single-digit
        # numbers matching a sequence like 1,2,3,4,5,6,7,8
        vals = list(non_empty.values())
        if len(vals) >= 3:
            single_digits = sum(1 for v in vals if v and re.match(r"^\d$", v.strip()))
            if single_digits / len(vals) > 0.6:
                continue  # Skip — this is a page-number header row

        # Check if this is a continuation row (no POS value)
        has_pos = bool(pos_header and row.get(pos_header))

        if has_pos:
            # This is a primary data row
            cleaned.append(row)
        elif cleaned and len(non_empty) >= 1:
            # Merge into the last data row
            prev = cleaned[-1]
            for k, v in row.items():
                if v and k in prev:
                    if prev[k]:
                        # Append to existing value (e.g. hardness annotation)
                        prev[k] = prev[k] + " " + v
                    else:
                        prev[k] = v
        # else: orphan continuation row with no preceding data row — skip

    return cleaned


def _find_header_by_word_positions(
    all_words: list[tuple[float, float, float, float, str, int]],
) -> tuple[float, list[tuple[float, float, str]]] | None:
    """Find the header row by scanning for a y-level with multiple keyword hits.

    Returns (header_y, [(x_start, x_end, col_name), ...]) or None.
    """
    # Group words by approximate y-position (within tolerance) — first page only
    y_tolerance = 4.0
    y_groups: dict[float, list[tuple[float, float, float, float, str]]] = {}
    for x0, y0, x1, y1, text, _page in all_words:
        if _page > 0:
            continue
        rounded_y = round(y0 / y_tolerance) * y_tolerance
        y_groups.setdefault(rounded_y, []).append((x0, y0, x1, y1, text))

    # Score each y-group for keyword matches
    best_y: float | None = None
    best_score = 0
    best_words: list[tuple[float, float, float, float, str]] = []

    for y_key, words in sorted(y_groups.items()):
        if len(words) < _MIN_COLS:
            continue
        score = 0
        for _, _, _, _, text in words:
            t = text.strip().lower()
            for kw in _HEADER_KEYWORDS:
                if kw in t:
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_y = y_key
            best_words = words

    if best_y is None or best_score < _MIN_COLS:
        return None

    # Build column definitions from header words
    best_words.sort(key=lambda w: w[0])

    columns: list[tuple[float, float, str]] = []
    i = 0
    while i < len(best_words):
        x0, y0, x1, y1, text = best_words[i]
        col_text = text.strip()
        col_x0 = x0
        col_x1 = x1

        # Merge adjacent words ONLY if they're close AND the next word is NOT
        # itself a recognised BOM keyword (to avoid merging "POS" + "BENENNUNG")
        while i + 1 < len(best_words):
            next_word = best_words[i + 1]
            gap = next_word[0] - col_x1
            next_text = next_word[4].strip().lower()
            next_is_keyword = any(kw in next_text for kw in _HEADER_KEYWORDS)

            if gap < 12 and not next_is_keyword:
                i += 1
                col_text += " " + best_words[i][4].strip()
                col_x1 = best_words[i][2]
            else:
                break
        columns.append((col_x0, col_x1, col_text))
        i += 1

    return best_y, columns


def _extract_rows_by_word_positions(
    all_words: list[tuple[float, float, float, float, str, int]],
    header_y: float,
    columns: list[tuple[float, float, str]],
    page_count: int,
) -> list[list[str | None]]:
    """Group words below the header y into rows, assigning to columns by x-position.

    Processes each page independently: detects the header y on each page
    (allowing for slight variation) and only includes data rows below it.
    """
    y_tolerance = 4.0
    num_cols = len(columns)

    # Build column boundaries: midpoints between consecutive column starts
    col_boundaries: list[float] = []
    for i in range(num_cols):
        if i == 0:
            col_boundaries.append(0)
        else:
            prev_end = columns[i - 1][1]
            this_start = columns[i][0]
            col_boundaries.append((prev_end + this_start) / 2)
    col_boundaries.append(99999)

    # Collect header keyword texts for detecting header repetitions
    header_texts = {col[2].lower() for col in columns}

    # Group words by page
    words_by_page: dict[int, list[tuple[float, float, float, float, str]]] = {}
    for x0, y0, x1, y1, text, page_num in all_words:
        words_by_page.setdefault(page_num, []).append((x0, y0, x1, y1, text))

    all_rows: list[list[str | None]] = []

    for page_num in sorted(words_by_page.keys()):
        page_words = words_by_page[page_num]

        # Find header y on this page (should be at same y or close to it)
        page_header_y = _find_header_y_on_page(page_words, header_y, y_tolerance)
        if page_header_y is None:
            # No header found on this page — skip (could be a cover page, etc.)
            continue

        # Collect data words: below header, excluding metadata area
        data_words = [w for w in page_words if w[1] > page_header_y + y_tolerance]
        if not data_words:
            continue

        # Group into y-bands (rows)
        data_words.sort(key=lambda w: (w[1], w[0]))
        row_bands: list[list[tuple[float, float, float, float, str]]] = []
        current_band: list[tuple[float, float, float, float, str]] = []
        current_y: float = -999

        for w in data_words:
            if abs(w[1] - current_y) > y_tolerance:
                if current_band:
                    row_bands.append(current_band)
                current_band = [w]
                current_y = w[1]
            else:
                current_band.append(w)
        if current_band:
            row_bands.append(current_band)

        # Assign words to columns for each row band
        for band in row_bands:
            cells: list[list[str]] = [[] for _ in range(num_cols)]
            for x0, y0, x1, y1, text in band:
                word_center = (x0 + x1) / 2
                col_idx = num_cols - 1
                for ci in range(num_cols):
                    if col_boundaries[ci] <= word_center < col_boundaries[ci + 1]:
                        col_idx = ci
                        break
                cells[col_idx].append(text.strip())

            row = [" ".join(c) if c else None for c in cells]
            non_empty = sum(1 for v in row if v)
            if non_empty < 1:
                continue
            if _is_word_row_header_repeat(row, columns):
                continue
            all_rows.append(row)

    return all_rows


def _find_header_y_on_page(
    page_words: list[tuple[float, float, float, float, str]],
    expected_y: float,
    tolerance: float,
) -> float | None:
    """Find the header y-position on a specific page.

    Looks for words near the expected y that match BOM keywords.
    """
    # Group by y
    y_groups: dict[float, list[str]] = {}
    for x0, y0, x1, y1, text in page_words:
        rounded = round(y0 / tolerance) * tolerance
        y_groups.setdefault(rounded, []).append(text.strip().lower())

    # Look for a y-group near the expected header_y with keyword matches
    best_y = None
    best_score = 0
    for y_val, texts in y_groups.items():
        if abs(y_val - expected_y) > 30:  # Allow some variation across pages
            continue
        score = 0
        for t in texts:
            for kw in _HEADER_KEYWORDS:
                if kw in t:
                    score += 1
                    break
        if score >= 3 and score > best_score:
            best_score = score
            best_y = y_val

    return best_y


def _is_word_row_header_repeat(
    row: list[str | None], columns: list[tuple[float, float, str]]
) -> bool:
    """Check if a data row is actually a repeated header."""
    matches = 0
    non_empty = 0
    for i, val in enumerate(row):
        if val is None:
            continue
        non_empty += 1
        col_name = columns[i][2].lower()
        if val.strip().lower() == col_name or col_name in val.strip().lower():
            matches += 1
    return non_empty > 0 and matches / non_empty > 0.4


# ---------------------------------------------------------------------------
# Merge tables + header detection (for Strategy 1)
# ---------------------------------------------------------------------------


def _merge_tables_with_header_detection(
    all_tables: list[list[list[str | None]]],
    target_cols: int,
) -> tuple[list[str | None] | None, list[list[str | None]], int]:
    """Merge tables across pages, detecting the real header row.

    Returns (header_row, data_rows, header_row_index).
    """
    # 1. Collect ALL raw rows from compatible tables (same col count ±2)
    raw_rows: list[list[str | None]] = []
    first_table_len = 0

    for table in all_tables:
        if not table:
            continue
        cols = max(len(r) for r in table)
        if abs(cols - target_cols) > 2:
            continue

        for row in table:
            padded = list(row) + [None] * (target_cols - len(row))
            raw_rows.append(padded[:target_cols])

        if not first_table_len:
            first_table_len = len(table)

    if not raw_rows:
        return None, [], -1

    # 2. Find the best header row among the first N rows of the first table
    scan_limit = min(10, first_table_len, len(raw_rows))
    header_idx = _find_header_row(raw_rows[:scan_limit], target_cols)

    if header_idx < 0:
        # Fallback: use row 0
        header_idx = 0

    header = raw_rows[header_idx]

    # 3. Collect data rows (everything after header, skipping header repeats)
    data_rows: list[list[str | None]] = []
    for row in raw_rows[header_idx + 1 :]:
        # Skip empty rows
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
            continue
        # Skip repeated headers
        if _is_repeated_header(row, header):
            continue
        # Skip metadata/title rows that appear before data on subsequent pages
        # (they would have been collected again from tables on later pages)
        data_rows.append(row)

    return header, data_rows, header_idx


# --- BOM header keywords across common languages ---
_HEADER_KEYWORDS = {
    # Position/number
    "pos",
    "position",
    "nr",
    "no",
    "num",
    "number",
    "detail",
    # Count/quantity
    "qty",
    "quantity",
    "anzahl",
    "stk",
    "stck",
    "qua",
    "count",
    "množství",
    "pcs",
    # Description
    "benennung",
    "bezeichnung",
    "description",
    "popis",
    "name",
    "teilename",
    "bauteil",
    "teil",
    "generica",
    "specifica",
    "denominazione",
    # Material
    "material",
    "werkst",
    "werkstoff",
    "matériau",
    "materiale",
    "norma",
    # Dimensions
    "dimension",
    "fertigma",
    "maße",
    "masse",
    "rohmass",
    "rohma",
    "finish",
    "x / ø",
    "y / l",
    "z /",
    # Hardness
    "härte",
    "hrc",
    "hrb",
    "hardness",
    "durezza",
    "tepelzprac",
    # Part number / article number
    "artikelnr",
    "sachnummer",
    "part",
    "dílu",
    "modellname",
    # Other BOM fields
    "supplier",
    "dodavatel",
    "lieferant",
    "manufacturer",
    "hersteller",
    "remark",
    "bemerkung",
    "hinweis",
    "note",
    "blatt",
    "foglio",
    "spare",
    "ersatz",
    "náhradní",
    "coating",
    "nitriding",
    "behandlung",
    "weight",
    "gewicht",
    "massa",
    "kg",
}


def _find_header_row(candidate_rows: list[list[str | None]], target_cols: int) -> int:
    """Score each candidate row on how 'header-like' it is and return the best index.

    A good header row has:
    - Many non-empty cells (high fill ratio)
    - Cells that are short text (not data-length values or long paragraphs)
    - Cells that match known BOM header keywords
    - Mostly non-numeric cells (headers are text, data rows have numbers)
    """
    best_idx = 0
    best_score = -1.0

    for idx, row in enumerate(candidate_rows):
        score = _score_header_row(row, target_cols)
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


def _score_header_row(row: list[str | None], target_cols: int) -> float:
    """Score a single row on how likely it is to be the header."""
    if not row:
        return -1.0

    non_empty = 0
    keyword_hits = 0
    short_text_count = 0
    numeric_count = 0

    for cell in row:
        text = _norm(cell)
        if not text:
            continue
        non_empty += 1

        # Check if cell text matches any known header keyword
        for kw in _HEADER_KEYWORDS:
            if kw in text:
                keyword_hits += 1
                break

        # Short text (1-40 chars) = typical header. Long text = data or title.
        if 1 <= len(text) <= 40:
            short_text_count += 1

        # Mostly numeric = data row, not header
        digits = sum(1 for c in text if c.isdigit())
        if len(text) > 0 and digits / len(text) > 0.6:
            numeric_count += 1

    if non_empty == 0:
        return -1.0

    fill_ratio = non_empty / target_cols
    keyword_ratio = keyword_hits / non_empty if non_empty else 0
    short_ratio = short_text_count / non_empty if non_empty else 0
    numeric_ratio = numeric_count / non_empty if non_empty else 0

    # Weighted score:
    # - keyword_ratio is the strongest signal (x3)
    # - fill_ratio rewards rows with more non-empty cells (x2)
    # - short_ratio rewards concise text (x1)
    # - numeric_ratio penalizes rows that look like data (x-2)
    score = (
        keyword_ratio * 3.0 + fill_ratio * 2.0 + short_ratio * 1.0 - numeric_ratio * 2.0
    )

    return score


def _is_repeated_header(row: list[str | None], header: list[str | None]) -> bool:
    """Check if a row is a repeat of the header."""
    matches = 0
    total = 0
    for a, b in zip(row, header):
        if a is None and b is None:
            continue
        total += 1
        a_str = _norm(a)
        b_str = _norm(b)
        if a_str and b_str and (a_str == b_str or a_str in b_str or b_str in a_str):
            matches += 1
    return total > 0 and matches / total > 0.5


def _norm(v: str | None) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().lower()


# ---------------------------------------------------------------------------
# Strategy 2: Text extraction + line-based parsing
# ---------------------------------------------------------------------------


def _try_text_extraction(
    doc: fitz.Document, filepath: Path, page_count: int
) -> ParsedBOM | None:
    """Fall back to extracting text and parsing line-by-line.

    This works for PDFs where find_tables() fails (e.g. CAD frames, fragmented tables).
    We extract all text, identify column headers, then parse each line as a data row.
    """
    all_text = ""
    for page in doc:
        all_text += page.get_text() + "\n---PAGE_BREAK---\n"

    if len(all_text.strip()) < 100:
        return None

    lines = all_text.split("\n")

    # Try to find a structured header line
    header_line_idx, headers = _find_text_headers(lines)
    if headers is None or len(headers) < _MIN_COLS:
        # Try the column-aligned approach
        return _try_column_aligned_parsing(lines, filepath, page_count)

    # Parse data rows based on the found headers
    rows = _parse_text_rows(lines, header_line_idx, len(headers))
    if len(rows) < _MIN_DATA_ROWS:
        return _try_column_aligned_parsing(lines, filepath, page_count)

    row_dicts = _rows_to_dicts(rows, headers)

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=page_count,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.6,
        ),
        headers=headers,
        rows=row_dicts,
        metadata={"parsing_mode": "text_line_based"},
    )


def _find_text_headers(lines: list[str]) -> tuple[int, list[str] | None]:
    """Find the line that looks like column headers."""
    # Known header patterns from Phase 1 analysis
    header_keywords = [
        "pos",
        "position",
        "benennung",
        "bezeichnung",
        "description",
        "material",
        "werkst",
        "fertigma",
        "dimension",
        "qty",
        "stk",
        "stck",
        "anzahl",
        "quantity",
        "detail number",
    ]

    for i, line in enumerate(lines[:100]):
        lower = line.lower()
        matches = sum(1 for kw in header_keywords if kw in lower)
        if matches >= 3:
            # This looks like a header line — split by multiple spaces or tabs
            parts = re.split(r"\s{2,}|\t", line.strip())
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= _MIN_COLS:
                return i, parts
    return -1, None


def _parse_text_rows(
    lines: list[str], header_idx: int, num_cols: int
) -> list[list[str | None]]:
    """Parse lines after the header, splitting on multi-space boundaries."""
    rows = []
    for line in lines[header_idx + 1 :]:
        if "---PAGE_BREAK---" in line:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        # Skip header repetitions
        parts = re.split(r"\s{2,}|\t", stripped)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            # Pad or truncate to match header count
            padded = parts[:num_cols] + [None] * max(0, num_cols - len(parts))
            rows.append(padded)
    return rows


def _try_column_aligned_parsing(
    lines: list[str], filepath: Path, page_count: int
) -> ParsedBOM | None:
    """Last resort: try to extract position-based records from text.

    Look for lines starting with a number (position) and gather structured data.
    """
    position_pattern = re.compile(r"^\s*(\d{1,4}[a-z]?)\s+(.+)", re.IGNORECASE)
    records: list[dict[str, str | None]] = []

    for line in lines:
        if "---PAGE_BREAK---" in line:
            continue
        m = position_pattern.match(line.strip())
        if m:
            pos = m.group(1)
            rest = m.group(2).strip()
            records.append({"Position": pos, "Inhalt": rest})

    if len(records) < _MIN_DATA_ROWS:
        return None

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=page_count,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.3,
        ),
        headers=["Position", "Inhalt"],
        rows=records,
        metadata={"parsing_mode": "column_aligned_fallback"},
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _clean_header_row(raw: list[str | None], target_cols: int) -> list[str]:
    """Convert raw header to clean deduplicated strings."""
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx in range(target_cols):
        val = raw[idx] if idx < len(raw) else None
        h = ""
        if val is not None:
            h = re.sub(r"\s+", " ", str(val)).strip()
        if not h:
            h = f"_col_{idx + 1}"
        # Deduplicate
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        headers.append(h)
    return headers


def _rows_to_dicts(
    rows: list[list[str | None]], headers: list[str]
) -> list[dict[str, str | None]]:
    """Convert row lists into dicts keyed by header names."""
    result = []
    for row in rows:
        d: dict[str, str | None] = {}
        for i, hdr in enumerate(headers):
            val = row[i] if i < len(row) else None
            if isinstance(val, str):
                val = val.strip() if val.strip() else None
            elif val is not None:
                val = str(val)
            d[hdr] = val
        result.append(d)
    return result


def _estimate_confidence(num_rows: int, page_count: int, col_count: int) -> float:
    """Heuristic confidence for table extraction quality."""
    if num_rows == 0:
        return 0.0
    # More rows relative to pages = more confident
    rows_per_page = num_rows / max(page_count, 1)
    if rows_per_page >= 5:
        score = 0.9
    elif rows_per_page >= 2:
        score = 0.8
    elif rows_per_page >= 0.5:
        score = 0.6
    else:
        score = 0.4
    # Penalize very few columns
    if col_count < 5:
        score -= 0.1
    return max(0.0, min(1.0, score))
