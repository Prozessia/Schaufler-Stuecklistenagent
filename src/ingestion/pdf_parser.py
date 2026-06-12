"""PDF Parser — GPT-4o Vision-first extraction pipeline.

Strategy:
  Phase A: Detect column structure from page 1 via GPT-4o Vision (1 call)
  Phase B: Dual Extraction — extract all data rows TWICE with different prompts,
           compare results, flag mismatches. Both runs happen in parallel per page.
  Post-validation: Schema-based plausibility checks per cell

Legacy parsers (PyMuPDF find_tables, word-position) are kept in
pdf_parser_legacy.py for debugging/comparison but are NOT used in production.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF — used only for page rendering, not table extraction

from src.core.models import (
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceMetadata,
)
from src.ingestion.pdf_common import ExtractionError, open_pdf_document
from src.ingestion.file_router import infer_customer
from src.ingestion.row_classifier import classify_non_data_rows
from src.llm.base import BaseLLM

logger = logging.getLogger(__name__)

_TEXT_LAYER_MIN_CHARS = 50

# ---------------------------------------------------------------------------
# Minimum thresholds
# ---------------------------------------------------------------------------
_MIN_COLS = 3
_MIN_DATA_ROWS = 2
# C1: fraction of page height treated as header/footer margin. Text blocks whose
# vertical center falls in the top or bottom margin are excluded from the BOM
# text extraction (page numbers, revision stamps, titles). Text-path only.
# Kept as a module constant so it can later be driven from YAML without code change.
_HEADER_FOOTER_MARGIN = 0.08
_MAX_CONCURRENT_PAGES = 1  # Sequenziell – vermeidet 429 Rate-Limit beim POC
_MAX_PHASE_B_JSON_RETRIES = 1

_PHASE_B_JSON_RETRY_SUFFIX = """

WICHTIG:
- Gib AUSSCHLIESSLICH gültiges JSON zurück (kein Markdown, keine Code-Fences).
- Nutze exakt das geforderte Schema mit dem Key "rows" als Array.
- Wenn keine Datenzeilen erkannt werden: "rows": []
"""


# ===================================================================
# Public API
# ===================================================================


async def parse_pdf(
    filepath: Path | str,
    llm: BaseLLM,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ParsedBOM:
    """Vision-first PDF parsing pipeline.

    1. Render every page to a 300 DPI PNG (base64).
    2. Phase A — detect column structure from page 1.
    3. Phase B — extract data rows from every page.
    4. Post-validate extracted values.
    5. Return unified ParsedBOM.

    progress_callback(done_pages, total_pages) is called after each page
    is processed.  Exceptions in the callback are swallowed so they can
    never abort the pipeline.
    """
    filepath = Path(filepath)
    images = _render_pdf_pages(filepath, dpi=250)

    if not images:
        raise ExtractionError(f"PDF has no renderable pages: {filepath}")

    page_count = len(images)

    # Phase A — column structure (page 1 only)
    detected_columns = await _detect_columns_via_vision(images[0], llm)

    if not detected_columns or len(detected_columns) < _MIN_COLS:
        # Retry with extended prompt
        detected_columns = await _detect_columns_via_vision(images[0], llm, retry=True)

    if not detected_columns or len(detected_columns) < _MIN_COLS:
        raise ExtractionError(
            f"Could not detect column structure ({len(detected_columns or [])} cols) "
            f"in: {filepath.name}"
        )

    logger.info(
        "Phase A: detected %d columns in %s: %s",
        len(detected_columns),
        filepath.name,
        detected_columns,
    )

    # Phase B — dual extract data rows from all pages.
    # detected_columns may be extended by BUG-017 per-page re-detect.
    (
        all_rows,
        dual_mismatches,
        dual_row_count_delta,
        detected_columns,
    ) = await _extract_all_pages_via_vision(
        images, detected_columns, llm, progress_callback=progress_callback
    )

    # B2: capture the PDF-side position set from the RAW vision rows, BEFORE
    # deduplication/post-validation can drop any. This is the Vision-path source
    # of truth for the reconciler (no text layer needed).
    # Hard limit: positions the Vision model never read are also absent here —
    # there is no ground truth to recover those without a text layer.
    raw_pdf_positions = _collect_position_values(all_rows, detected_columns)
    # B2/BUG-011: also capture per-position occurrence counts from the same RAW
    # rows (before dedup). Used by the reconciler to detect under-extraction when
    # multiple rows share the same position number.
    raw_pdf_position_counts = _collect_position_counts(all_rows, detected_columns)

    # Deduplicate rows that appear on multiple pages (header repeats etc.)
    all_rows = _deduplicate_rows(all_rows, detected_columns)

    # Post-validation: flag suspicious values
    all_rows, row_validation_flags = _post_validate_extraction(
        all_rows, detected_columns
    )

    # Coordinate cross-check: validate extracted values against PDF text layer
    # using fuzzy spatial matching (IoU + row corridor).
    (
        coord_mismatches,
        coord_confirmations,
        coord_row_corridors,
        coord_column_conflicts,
        coord_detail_proofs,
        source_locations,
        has_text_layer,
    ) = _cross_check_with_text_layer(filepath, all_rows, detected_columns)
    coord_mismatch_count = sum(len(f) for f in coord_mismatches.values())
    coord_confirmed_count = sum(len(f) for f in coord_confirmations.values())
    coord_rowcorr_count = sum(len(f) for f in coord_row_corridors.values())
    coord_colconflict_count = sum(len(f) for f in coord_column_conflicts.values())
    coord_detailproof_count = sum(len(f) for f in coord_detail_proofs.values())

    # Merge coordinate mismatch flags with prefix "COORDMISS:"
    for ridx, tflags in coord_mismatches.items():
        if ridx not in row_validation_flags:
            row_validation_flags[ridx] = []
        row_validation_flags[ridx].extend(f"COORDMISS:{f}" for f in tflags)

    # Merge strong coordinate confirmations with prefix "COORDOK:"
    for ridx, tflags in coord_confirmations.items():
        if ridx not in row_validation_flags:
            row_validation_flags[ridx] = []
        row_validation_flags[ridx].extend(f"COORDOK:{f}" for f in tflags)

    # Merge coarse row-corridor confirmations with prefix "COORDROW:"
    for ridx, tflags in coord_row_corridors.items():
        if ridx not in row_validation_flags:
            row_validation_flags[ridx] = []
        row_validation_flags[ridx].extend(f"COORDROW:{f}" for f in tflags)

    # Merge X-axis column conflicts with prefix "COORDCOL:"
    for ridx, tflags in coord_column_conflicts.items():
        if ridx not in row_validation_flags:
            row_validation_flags[ridx] = []
        row_validation_flags[ridx].extend(f"COORDCOL:{f}" for f in tflags)

    # Merge relaxed Detail Number proofs with prefix "COORDDET:"
    for ridx, tflags in coord_detail_proofs.items():
        if ridx not in row_validation_flags:
            row_validation_flags[ridx] = []
        row_validation_flags[ridx].extend(f"COORDDET:{f}" for f in tflags)

    # Merge dual-extraction mismatches into validation flags
    # Use prefix "DUAL:" so the scorer can distinguish them
    for ridx, mismatch_flags in dual_mismatches.items():
        if ridx not in row_validation_flags:
            row_validation_flags[ridx] = []
        row_validation_flags[ridx].extend(f"DUAL:{f}" for f in mismatch_flags)

    validation_flag_count = sum(len(flags) for flags in row_validation_flags.values())
    dual_mismatch_count = sum(len(f) for f in dual_mismatches.values())

    logger.info(
        "Vision extraction: %s → %d rows, %d columns, "
        "%d validation flags, %d dual mismatches, "
        "%d coord mismatches, %d coord confirmed, %d coord row-corridor, "
        "%d coord column-conflicts, %d coord detail-proofs, text_layer=%s",
        filepath.name,
        len(all_rows),
        len(detected_columns),
        validation_flag_count,
        dual_mismatch_count,
        coord_mismatch_count,
        coord_confirmed_count,
        coord_rowcorr_count,
        coord_colconflict_count,
        coord_detailproof_count,
        has_text_layer,
    )

    # Lossless footer/header/note detection: tag rows that look like non-data so
    # a reviewer can confirm. Never drops a row (rows=all_rows is unchanged) and
    # feeds no scoring signal — zero-data-loss and zero-false-green are untouched.
    non_data_row_flags = classify_non_data_rows(all_rows, detected_columns)

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=page_count,
            extraction_method=ExtractionMethod.GPT4O_VISION,
            extraction_confidence=0.92,
        ),
        headers=detected_columns,
        rows=all_rows,
        expected_position_count=_count_distinct_positions(all_rows, detected_columns),
        raw_pdf_positions=raw_pdf_positions,
        raw_pdf_position_counts=raw_pdf_position_counts,
        metadata={
            "extraction_method": "gpt4o_vision",
            "dual_extraction": True,
            "text_layer_crosscheck": True,
            "has_text_layer": has_text_layer,
            "document_text_pages": (
                _extract_pdf_page_texts(filepath) if has_text_layer else []
            ),
            "document_text_layer": (
                _read_document_text_layer(filepath) if has_text_layer else ""
            ),
            "pages_processed": page_count,
            "total_rows": len(all_rows),
            "columns_detected": len(detected_columns),
            "validation_flags_total": validation_flag_count,
            "dual_mismatch_count": dual_mismatch_count,
            "dual_row_count_delta": dual_row_count_delta,
            "coord_mismatch_count": coord_mismatch_count,
            "coord_confirmed_count": coord_confirmed_count,
            "coord_rowcorr_count": coord_rowcorr_count,
            "coord_colconflict_count": coord_colconflict_count,
            "coord_detailproof_count": coord_detailproof_count,
            "row_validation_flags": row_validation_flags,
            "non_data_row_flags": non_data_row_flags,
            "source_locations": source_locations,
        },
    )


def pdf_has_text_layer(
    filepath: Path | str, min_chars: int = _TEXT_LAYER_MIN_CHARS
) -> bool:
    """Return True when the PDF exposes a usable text layer."""
    doc = open_pdf_document(filepath)
    try:
        text_len = 0
        for page in doc:
            text_len += len(page.get_text("text").strip())
            if text_len >= min_chars:
                return True
        return False
    finally:
        doc.close()


# ===================================================================
# Page rendering (PyMuPDF → PNG → base64)
# ===================================================================


def _render_pdf_pages(filepath: Path, dpi: int = 300) -> list[str]:
    """Render each page as a base64-encoded PNG string."""
    doc = open_pdf_document(filepath)
    zoom = dpi / 72  # default PDF resolution is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    images: list[str] = []

    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        images.append(b64)

    doc.close()
    return images


def _extract_pdf_page_texts(filepath: Path) -> list[str]:
    """Extract layout-aware text per page from a text-based PDF."""
    doc = open_pdf_document(filepath)
    try:
        texts: list[str] = []
        for page in doc:
            cleaned = _render_layout_aware_page_text(page)
            if cleaned:
                texts.append(cleaned)
        return texts
    finally:
        doc.close()


def _serialize_document_text_layer(page_texts: list[str]) -> str:
    return "\n\n--- PAGE BREAK ---\n\n".join(text for text in page_texts if text)


def _read_document_text_layer(filepath: Path) -> str:
    return _serialize_document_text_layer(_extract_pdf_page_texts(filepath))


def _render_layout_aware_page_text(page: fitz.Page) -> str:
    """Render a page into row-banded, coordinate-aware text for the LLM."""
    blocks = _extract_text_blocks(page)
    if not blocks:
        return page.get_text("text").strip()

    heights = sorted(max(block["y1"] - block["y0"], 1.0) for block in blocks)
    median_height = heights[len(heights) // 2] if heights else 8.0
    row_tolerance = min(18.0, max(4.0, median_height * 0.55))

    row_bands: list[dict[str, object]] = []
    for block in blocks:
        center_y = (block["y0"] + block["y1"]) / 2
        if row_bands:
            current = row_bands[-1]
            current_center = float(current["center_y"])
            current_max_y = float(current["max_y"])
            if abs(center_y - current_center) <= row_tolerance or block["y0"] <= (
                current_max_y + row_tolerance / 2
            ):
                current_blocks = current["blocks"]
                assert isinstance(current_blocks, list)
                current_blocks.append(block)
                current["center_y"] = (current_center + center_y) / 2
                current["max_y"] = max(current_max_y, block["y1"])
                continue

        row_bands.append(
            {
                "center_y": center_y,
                "max_y": block["y1"],
                "blocks": [block],
            }
        )

    rendered_rows: list[str] = []
    for row_index, band in enumerate(row_bands, start=1):
        band_blocks = band["blocks"]
        assert isinstance(band_blocks, list)
        cells = _merge_layout_row_cells(band_blocks)
        if not cells:
            continue
        rendered_cells = [
            f"[x={int(cell['x0'])}-{int(cell['x1'])}] {cell['text']}" for cell in cells
        ]
        rendered_rows.append(f"ROW {row_index:03d}: " + " || ".join(rendered_cells))

    if rendered_rows:
        return "\n".join(rendered_rows)

    return page.get_text("text").strip()


def _extract_text_blocks(page: fitz.Page) -> list[dict[str, float | str]]:
    """Extract non-empty text blocks sorted by their PDF coordinates.

    C1: blocks whose vertical center lies in the top/bottom header/footer margin
    (``_HEADER_FOOTER_MARGIN`` of the page height) are excluded so that page
    numbers, revision stamps and titles cannot leak into the BOM extraction.
    """
    try:
        raw_blocks = page.get_text("blocks", sort=True)
    except TypeError:
        raw_blocks = page.get_text("blocks")

    page_rect = getattr(page, "rect", None)
    page_height = float(getattr(page_rect, "height", 0.0) or 0.0)
    top_cut = _HEADER_FOOTER_MARGIN * page_height
    bottom_cut = (1.0 - _HEADER_FOOTER_MARGIN) * page_height

    blocks: list[dict[str, float | str]] = []
    for raw_block in raw_blocks or []:
        if not isinstance(raw_block, (tuple, list)) or len(raw_block) < 5:
            continue

        x0, y0, x1, y1, text = raw_block[:5]
        cleaned_text = _normalize_layout_block_text(text)
        if not cleaned_text:
            continue

        # C1: drop header/footer bands (only when a valid page height is known).
        if page_height > 0:
            center_y = (float(y0) + float(y1)) / 2
            if center_y < top_cut or center_y > bottom_cut:
                logger.debug(
                    "Header/footer filter: dropped block at y=%.1f (cut <%.1f / >%.1f): %r",
                    center_y,
                    top_cut,
                    bottom_cut,
                    cleaned_text[:60],
                )
                continue

        blocks.append(
            {
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "text": cleaned_text,
            }
        )

    blocks.sort(key=lambda block: (block["y0"], block["x0"], block["y1"], block["x1"]))
    return blocks


def _normalize_layout_block_text(text: object) -> str:
    """Normalize a text block while preserving logical multiline content."""
    if not isinstance(text, str):
        return ""

    parts: list[str] = []
    for line in text.splitlines():
        normalized_line = re.sub(r"\s+", " ", line).strip()
        if normalized_line and (not parts or parts[-1] != normalized_line):
            parts.append(normalized_line)

    return " / ".join(parts)


def _merge_layout_row_cells(
    row_blocks: list[dict[str, float | str]],
) -> list[dict[str, float | str]]:
    """Merge blocks that belong to the same logical column corridor."""
    if not row_blocks:
        return []

    merged_cells: list[dict[str, float | str]] = []
    for block in sorted(row_blocks, key=lambda item: (item["x0"], item["y0"])):
        if merged_cells and _same_column_corridor(merged_cells[-1], block):
            previous_text = str(merged_cells[-1]["text"])
            block_text = str(block["text"])
            if block_text and block_text not in previous_text:
                merged_cells[-1]["text"] = f"{previous_text} / {block_text}"
            merged_cells[-1]["x0"] = min(
                float(merged_cells[-1]["x0"]),
                float(block["x0"]),
            )
            merged_cells[-1]["x1"] = max(
                float(merged_cells[-1]["x1"]),
                float(block["x1"]),
            )
            merged_cells[-1]["y0"] = min(
                float(merged_cells[-1]["y0"]),
                float(block["y0"]),
            )
            merged_cells[-1]["y1"] = max(
                float(merged_cells[-1]["y1"]),
                float(block["y1"]),
            )
            continue

        merged_cells.append(dict(block))

    return merged_cells


def _same_column_corridor(
    left: dict[str, float | str],
    right: dict[str, float | str],
    x_tolerance: float = 18.0,
) -> bool:
    """Return True when two blocks appear to belong to the same column."""
    left_x0 = float(left["x0"])
    right_x0 = float(right["x0"])
    left_x1 = float(left["x1"])
    right_x1 = float(right["x1"])

    overlap = max(0.0, min(left_x1, right_x1) - max(left_x0, right_x0))
    min_width = max(1.0, min(left_x1 - left_x0, right_x1 - right_x0))
    overlap_ratio = overlap / min_width

    return abs(left_x0 - right_x0) <= x_tolerance or overlap_ratio >= 0.6


# ===================================================================
# Phase A — Column structure detection
# ===================================================================

_PHASE_A_SYSTEM = (
    "Du bist ein Experte für die Analyse technischer Stücklisten (Bill of Materials). "
    "Du bekommst ein Bild einer PDF-Seite und identifizierst die Tabellenstruktur. "
    "Antworte ausschließlich in JSON."
)

_PHASE_A_PROMPT = """\
Du siehst Seite 1 einer technischen Stückliste (Bill of Materials) im PDF-Format.

Deine Aufgabe: Identifiziere die Spaltenstruktur der Stücklisten-Tabelle.

IGNORIERE komplett:
- Firmenlogo, Adresse, Telefonnummer, Firmennamen
- CAD-Zeichnungsrahmen-Markierungen (Zahlen 1-8 am Rand, Buchstaben A-E am Rand)
- Metadaten wie TEILNAME, Materialnummer, Werkzeug-Nr, Datum, Aussteller, Unterschrift
- Seitenzahlen ("Blatt X v. Y", "Seite X von Y")
- Legenden oder Erklärungstexte

FINDE die Tabellen-Spaltenüberschriften. Typische Spalten in Stücklisten sind z.B.:
POS, Position, Pos. Nr., Detail Number, Pozice
BENENNUNG, Bezeichnung, Description, Teil, POPIS
STK, Stück, Anzahl, Menge, QTY, Quantity, Množství
WERKSTOFF, Material, Werkst., WERKST, Matériau
FERTIGMASS, Fertigmaße, Dimensions, Abmaß, ČistýRozměr
HÄRTE, Härte HRC, Hardness, Rc
... und weitere

Gib die Spaltenbezeichnungen als JSON-Array zurück, in der Reihenfolge wie sie in der Tabelle erscheinen (von links nach rechts).

Antwortformat (NUR JSON, kein anderer Text):
{
  "columns": ["POS", "BENENNUNG", "STK", "WERKST", "FERTIGMASS", ...],
  "header_row_found": true,
  "language_detected": "de",
  "notes": "Optional: Besonderheiten der Tabelle"
}
"""

_PHASE_A_RETRY_PROMPT = """\
Du siehst Seite 1 einer technischen Stückliste (Bill of Materials).

Beim ersten Versuch wurden zu wenige Spalten erkannt. Bitte schaue GENAUER hin.

Die Tabelle kann:
- Sehr kleine Schriftgröße haben
- In einem CAD-Zeichnungsrahmen eingebettet sein
- Spaltenüberschriften über mehrere Zeilen verteilt haben
- Abgekürzte Spaltennamen nutzen (z.B. "STK" statt "Stückzahl")

Suche nach JEDER Spalte in der Stücklisten-Tabelle. Ignoriere CAD-Rahmen-Elemente.

Antwortformat (NUR JSON):
{
  "columns": ["Spalte1", "Spalte2", ...],
  "header_row_found": true,
  "language_detected": "de",
  "notes": "Beschreibe was du siehst"
}
"""


async def _detect_columns_via_vision(
    page_image_b64: str,
    llm: BaseLLM,
    retry: bool = False,
) -> list[str] | None:
    """Phase A: detect column headers from page 1 image via GPT-4o Vision."""
    prompt = _PHASE_A_RETRY_PROMPT if retry else _PHASE_A_PROMPT

    response = await llm.complete_with_image(
        system=_PHASE_A_SYSTEM,
        user=prompt,
        image_b64=page_image_b64,
        json_mode=True,
        temperature=0.0,
        max_tokens=2048,
    )

    data = _safe_json_loads(response.content, "Phase A")
    if not isinstance(data, dict):
        return None

    columns = data.get("columns", [])
    if not isinstance(columns, list):
        return None

    # Clean column names
    columns = [str(c).strip() for c in columns if c and str(c).strip()]

    # Validate: at least one column looks like a BOM field
    bom_keywords = {
        "pos",
        "position",
        "nr",
        "no",
        "detail",
        "benennung",
        "bezeichnung",
        "description",
        "popis",
        "qty",
        "quantity",
        "stk",
        "stück",
        "menge",
        "anzahl",
        "material",
        "werkst",
        "werkstoff",
        "fertigma",
        "dimension",
        "härte",
        "hardness",
        "hrc",
        "rohmass",
        "rohma",
        "bemerkung",
        "remark",
        "info",
        "code",
        "part",
        "sachnummer",
        "artikelnr",
        "dílu",
        "množství",
        "peso",
        "gewicht",
        "weight",
        "supplier",
        "lieferant",
        "spare",
        "coating",
        "nitriding",
        "behandlung",
    }
    has_bom_column = any(
        any(kw in col.lower() for kw in bom_keywords) for col in columns
    )

    if not has_bom_column and len(columns) < 3:
        logger.warning("Phase A: no BOM-like columns found in: %s", columns)
        return None

    logger.info(
        "Phase A: LLM detected %d columns, %d tokens in / %d out",
        len(columns),
        response.tokens_input,
        response.tokens_output,
    )

    return columns


# ===================================================================
# Phase B — Extract data rows from all pages
# ===================================================================

_PHASE_B_SYSTEM = (
    "Du bist ein Experte für die Extraktion von Daten aus technischen Stücklisten. "
    "Du bekommst ein Bild einer PDF-Seite und extrahierst alle Datenzeilen. "
    "Antworte ausschließlich in JSON."
)

_PHASE_B_SYSTEM_ALT = (
    "Du bist ein OCR-Spezialist für technische Tabellen. "
    "Du liest jede Zeile der Tabelle Spalte für Spalte ab. "
    "Antworte ausschließlich in JSON."
)


def _build_phase_b_prompt(columns: list[str], page_num: int) -> str:
    """Build the data extraction prompt for a single page."""
    col_list = ", ".join(columns)
    col_json_template = ", ".join(f'"{c}": "Wert"' for c in columns)

    return f"""\
Du siehst eine Seite einer technischen Stückliste (Bill of Materials).

Die Tabelle hat folgende Spalten (in dieser Reihenfolge):
{col_list}

EXTRAHIERE alle Datenzeilen dieser Seite.

REGELN:
1. IGNORIERE komplett:
   - Firmenlogo, Adresse, Telefonnummer
   - CAD-Rahmen-Markierungen (Zahlen 1-8, Buchstaben A-E am Rand)
   - Wiederholte Spaltenüberschriften (Header-Zeilen)
   - Metadaten (TEILNAME, Materialnummer, Werkzeug-Nr, Datum, etc.)
   - Seitennummern, Legenden, Fußnoten

2. TRENNE Spalten korrekt:
   - Jeder Wert gehört zu genau einer Spalte
   - Wenn zwei Werte visuell nah beieinander stehen aber zu verschiedenen \
Spalten gehören, trenne sie korrekt
   - Beispiel: "1 T001_EINSATZ_BS" sind ZWEI Werte: \
Position=1 und Benennung=T001_EINSATZ_BS
   - Beispiel: "4 1-2343_ESU" sind ZWEI Werte: \
Stückzahl=4 und Werkstoff=1-2343_ESU

3. DATENTYP-HINWEISE:
   - Positionsnummer: Ganzzahl, 1-999, max 4 Stellen
   - Stückzahl/Menge: Kleine Ganzzahl, typisch 1-50
   - Fertigmaße: Format "Zahl x Zahl x Zahl" oder "Ø Zahl x Zahl"
   - Werkstoff: Beginnt oft mit "1." oder "1-" (z.B. 1.2343, 1-2343_ESU)
   - Härte: Enthält "HRC", "HB", oder "N/mm²"

4. Wenn eine Zeile KEINE Datenzeile ist (leer, nur Trennlinie, nur Metadaten), \
überspringe sie.

5. Wenn ein Feld leer ist, setze es auf null.

Antwortformat (NUR JSON, kein anderer Text):
{{
  "rows": [
    {{{col_json_template}}}
  ],
  "page_number": {page_num},
  "rows_extracted": 0,
  "notes": null
}}
"""


def _build_phase_b_prompt_alt(columns: list[str], page_num: int) -> str:
    """Build ALTERNATIVE extraction prompt (Prompt B for Dual Extraction).

    Different wording, same goal — forces a different 'reading path' through
    the image so that systematic misreads are caught by comparison.
    """
    col_list = ", ".join(columns)
    col_json_template = ", ".join(f'"{c}": "Wert"' for c in columns)

    return f"""\
Lies die Tabelle auf dieser Seite Zeile für Zeile, Spalte für Spalte ab.

Die Spalten der Tabelle sind (von links nach rechts):
{col_list}

VORGEHEN:
1. Gehe Zeile für Zeile von oben nach unten durch die Tabelle.
2. Für jede Zeile: lies den Wert in JEDER Spalte einzeln ab.
3. Achte besonders auf NUMERISCHE Werte (Stückzahl, Position, Mengen):
   - Lies die Zahl GENAU ab, verwechsle nicht 1/7, 2/7, 1/4, 3/8 etc.
   - Stückzahlen sind typisch kleine Ganzzahlen (1, 2, 4, 6, 8, 10...)
   - Positionsnummern sind fortlaufend (1, 2, 3, ... oder 10, 20, 30, ...)
4. Überspringe Header-Zeilen, Fußzeilen, Legenden, Logos.
5. Leere Felder = null.

WICHTIG: Jede Spalte EINZELN lesen — nicht zusammen mit der Nachbarspalte!

Antwortformat (NUR JSON):
{{
  "rows": [
    {{{col_json_template}}}
  ],
  "page_number": {page_num},
  "rows_extracted": 0,
  "notes": null
}}
"""


def _is_page_anomalous(
    rows_a: list[dict[str, str | None]],
    columns: list[str],
) -> bool:
    """Return True when rows_a looks like a wrong-schema extraction (BUG-017).

    A page is anomalous when it produced no rows at all, or when more than 50%
    of its rows have more than 50% of the detected columns as None — which is the
    hallmark of using the wrong schema for that page's table structure.
    """
    if not rows_a:
        return True
    n_cols = len(columns)
    if n_cols == 0:
        return False
    empty_row_count = sum(
        1
        for row in rows_a
        if sum(1 for c in columns if row.get(c) is None) > n_cols / 2
    )
    return (empty_row_count / len(rows_a)) > 0.5


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two sets of normalised column names."""
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


async def _process_single_page_with_redetect(
    idx: int,
    img: str,
    active_columns: list[str],
    llm: BaseLLM,
) -> tuple[
    list[dict[str, str | None]],
    list[dict[str, str | None]],
    list[str] | None,
]:
    """Extract one page (dual A+B) with optional BUG-017 re-detect.

    Returns (rows_a, rows_b, new_columns_or_None).
    new_columns_or_None is set only when a sufficiently different schema was
    detected for this page (Jaccard < 0.5); the caller merges it into
    active_columns after the wave completes.
    """
    page_num = idx + 1
    rows_a, rows_b = await asyncio.gather(
        _extract_single_page(img, active_columns, llm, page_num, variant="A"),
        _extract_single_page(img, active_columns, llm, page_num, variant="B"),
    )

    # BUG-017: anomaly check for pages >= 2 (page 1 always used for schema).
    new_cols_out: list[str] | None = None
    if page_num >= 2 and _is_page_anomalous(rows_a, active_columns):
        new_cols = await _detect_columns_via_vision(img, llm)
        if new_cols and len(new_cols) >= _MIN_COLS:
            existing_norm = {_normalize_alnum(c) for c in active_columns}
            new_norm = {_normalize_alnum(c) for c in new_cols}
            similarity = _jaccard_similarity(existing_norm, new_norm)
            if similarity < 0.5:
                logger.info(
                    "BUG-017: page %d schema anomaly detected (Jaccard=%.2f), "
                    "re-extracting with new columns: %s",
                    page_num,
                    similarity,
                    new_cols,
                )
                rows_a, rows_b = await asyncio.gather(
                    _extract_single_page(img, new_cols, llm, page_num, variant="A"),
                    _extract_single_page(img, new_cols, llm, page_num, variant="B"),
                )
                new_cols_out = new_cols

    return rows_a, rows_b, new_cols_out


async def _extract_all_pages_via_vision(
    images: list[str],
    detected_columns: list[str],
    llm: BaseLLM,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, str | None]], dict[int, list[str]], int, list[str]]:
    """Phase B: Dual Extraction — extract rows TWICE with different prompts.

    Both extractions run sequentially per page. Results are compared cell-by-cell
    using sequence alignment (BUG-016). Mismatches are flagged; the value from
    Extraction A is kept as primary.

    BUG-017: for pages >= 2 an anomaly check is performed on rows_a. If the page
    looks empty/wrong-schema (>50% of rows have >50% Nones), Phase A is re-run
    for that page. When the new column set is sufficiently different (Jaccard < 0.5)
    and has >= _MIN_COLS columns, the page is re-extracted with the new schema and
    any new column names are appended to detected_columns.  Maximum one re-detect
    per page, no retry loop.

    Returns (rows, dual_mismatch_flags, row_count_delta, final_detected_columns).

    PERF-001: limited page concurrency via VISION_PAGE_CONCURRENCY env var
    (default "1" → original sequential behaviour).  Pages are processed in
    waves of size N; BUG-017 anomaly handling happens inside each task and new
    columns from re-detection are merged sequentially after every wave, so the
    BUG-017 semantics are preserved.

    Always processes page-by-page (or wave-by-wave) — a single batch call
    shares one max_tokens budget across all pages, which truncates dense BOMs.
    Per-page extraction isolates the token budget per page.
    """
    # Read concurrency at call time (not module load) so tests can monkeypatch env.
    try:
        concurrency = int(os.environ.get("VISION_PAGE_CONCURRENCY", "1"))
        if concurrency < 1:
            concurrency = 1
    except (ValueError, TypeError):
        concurrency = 1

    # Work on a mutable copy so callers can see the extended column list.
    active_columns: list[str] = list(detected_columns)
    total_pages = len(images)

    all_rows_a: list[dict[str, str | None]] = []
    all_rows_b: list[dict[str, str | None]] = []
    done_pages = 0

    def _fire_progress() -> None:
        nonlocal done_pages
        done_pages += 1
        if progress_callback is not None:
            try:
                progress_callback(done_pages, total_pages)
            except Exception:  # noqa: BLE001
                pass

    if concurrency <= 1:
        # Original sequential path — identical to pre-PERF-001 behaviour.
        for idx, img in enumerate(images):
            rows_a, rows_b, new_cols = await _process_single_page_with_redetect(
                idx, img, active_columns, llm
            )
            if new_cols is not None:
                existing_set = set(active_columns)
                for col in new_cols:
                    if col not in existing_set:
                        active_columns.append(col)
                        existing_set.add(col)
            all_rows_a.extend(rows_a)
            all_rows_b.extend(rows_b)
            _fire_progress()
    else:
        # Wave-based parallel path: process up to `concurrency` pages at once.
        # After each wave, merge new columns (from BUG-017 re-detection) before
        # starting the next wave so subsequent pages see the updated schema.
        for wave_start in range(0, total_pages, concurrency):
            wave_indices = range(wave_start, min(wave_start + concurrency, total_pages))
            wave_tasks = [
                _process_single_page_with_redetect(
                    idx, images[idx], active_columns, llm
                )
                for idx in wave_indices
            ]
            wave_results = await asyncio.gather(*wave_tasks)

            # Merge results in page order, then update active_columns.
            existing_set = set(active_columns)
            for rows_a, rows_b, new_cols in wave_results:
                all_rows_a.extend(rows_a)
                all_rows_b.extend(rows_b)
                if new_cols is not None:
                    for col in new_cols:
                        if col not in existing_set:
                            active_columns.append(col)
                            existing_set.add(col)
                _fire_progress()

    mismatches, row_count_delta = _compare_dual_extractions(
        all_rows_a, all_rows_b, active_columns
    )
    if row_count_delta != 0:
        logger.warning(
            "Dual Extraction: row count delta=%d (A=%d rows, B=%d rows)",
            row_count_delta,
            len(all_rows_a),
            len(all_rows_b),
        )
    return all_rows_a, mismatches, row_count_delta, active_columns


async def _extract_single_page(
    image: str,
    columns: list[str],
    llm: BaseLLM,
    page_num: int,
    variant: str = "A",
) -> list[dict[str, str | None]]:
    """Extract rows from a single page."""
    if variant == "A":
        prompt = _build_phase_b_prompt(columns, page_num)
        system = _PHASE_B_SYSTEM
    else:
        prompt = _build_phase_b_prompt_alt(columns, page_num)
        system = _PHASE_B_SYSTEM_ALT

    response = await llm.complete_with_image(
        system=system,
        user=prompt,
        image_b64=image,
        json_mode=True,
        temperature=0.0,
        max_tokens=16384,
    )

    logger.info(
        "Phase B-%s page %d: %d tokens in / %d out",
        variant,
        page_num,
        response.tokens_input,
        response.tokens_output,
    )

    rows, parse_ok = _parse_extraction_response(
        response.content,
        columns,
        phase_label=f"Phase B-{variant} page {page_num}",
    )
    if parse_ok:
        return rows

    # Keep the best partial recovery seen so far; retries may still yield a
    # clean parse, but we never discard salvaged rows back to [].
    best_partial: list[dict[str, str | None]] = rows
    last_content = response.content

    retry_prompt = prompt + _PHASE_B_JSON_RETRY_SUFFIX
    for attempt in range(1, _MAX_PHASE_B_JSON_RETRIES + 1):
        retry_response = await llm.complete_with_image(
            system=system,
            user=retry_prompt,
            image_b64=image,
            json_mode=True,
            temperature=0.0,
            max_tokens=16384,
        )
        logger.info(
            "Phase B-%s page %d retry %d: %d tokens in / %d out",
            variant,
            page_num,
            attempt,
            retry_response.tokens_input,
            retry_response.tokens_output,
        )
        rows, parse_ok = _parse_extraction_response(
            retry_response.content,
            columns,
            phase_label=f"Phase B-{variant} page {page_num} retry-{attempt}",
        )
        if parse_ok:
            return rows
        if len(rows) > len(best_partial):
            best_partial = rows
        last_content = retry_response.content

    if best_partial:
        logger.warning(
            "Phase B-%s page %d: returning %d partially-recovered row(s) "
            "after JSON parse failures (truncation salvaged)",
            variant,
            page_num,
            len(best_partial),
        )
        return best_partial

    logger.error(
        "Phase B-%s page %d: total JSON parse failure, 0 rows recovered. "
        "Response snippet: %s",
        variant,
        page_num,
        (last_content or "")[:500],
    )
    return []


def _normalize_extraction_rows(
    raw_rows: list,
    columns: list[str],
) -> list[dict[str, str | None]]:
    """Normalize raw row dicts to the detected column schema, dropping empties."""
    result: list[dict[str, str | None]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        row: dict[str, str | None] = {}
        for col in columns:
            val = raw.get(col)
            if val is None or (isinstance(val, str) and not val.strip()):
                row[col] = None
            else:
                row[col] = str(val).strip()
        # Skip completely empty rows
        if any(v is not None for v in row.values()):
            result.append(row)
    return result


def _try_recover_partial_json(raw: str) -> list[dict]:
    """Salvage complete row objects from invalid/truncated JSON.

    When an LLM response is cut off mid-stream (e.g. max_tokens), the overall
    JSON is unparseable but the rows emitted before the cut are intact. This
    scanner walks the (optionally rows-array-focused) text and extracts every
    balanced, individually-parseable ``{...}`` object, ignoring the trailing
    incomplete one. String contents (including escaped quotes/braces) are
    respected so braces inside values don't corrupt the depth count.

    Returns the list of recovered dict objects (possibly empty). Never raises.
    """
    if not raw:
        return []

    text = _strip_llm_json_payload(raw) or raw

    # Focus on the rows array if present, so we don't pick up unrelated objects
    # such as a leading metadata object.
    rows_key = text.find('"rows"')
    if rows_key != -1:
        bracket = text.find("[", rows_key)
        if bracket != -1:
            text = text[bracket:]

    recovered: list[dict] = []
    depth = 0
    start: int | None = None
    in_str = False
    escaped = False

    for index, char in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
            continue

        if char == '"':
            in_str = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start : index + 1]
                    normalized = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        obj = json.loads(normalized)
                    except json.JSONDecodeError:
                        obj = None
                    if isinstance(obj, dict):
                        recovered.append(obj)
                    start = None

    return recovered


def _parse_extraction_response(
    content: str,
    columns: list[str],
    phase_label: str = "Phase B",
) -> tuple[list[dict[str, str | None]], bool]:
    """Parse the JSON response from Phase B and normalize row dicts.

    Returns (rows, parse_ok). On a clean parse parse_ok is True. If the clean
    parse fails but partial recovery salvages row objects from truncated JSON,
    rows are returned with parse_ok=False (so the caller may still retry for a
    clean response, but never loses the salvaged rows). Returns ([], False)
    only when nothing at all could be recovered.
    """
    data = _safe_json_loads(content, phase_label)
    if isinstance(data, dict):
        raw_rows = data.get("rows", [])
        if isinstance(raw_rows, list):
            return _normalize_extraction_rows(raw_rows, columns), True
        logger.warning("%s: JSON payload missing 'rows' list", phase_label)

    # Clean parse failed (invalid or truncated JSON) → attempt partial recovery.
    recovered = _try_recover_partial_json(content)
    if recovered:
        logger.warning(
            "%s: recovered %d row object(s) from invalid/truncated JSON",
            phase_label,
            len(recovered),
        )
        return _normalize_extraction_rows(recovered, columns), False

    return [], False


def _safe_json_loads(content: str, phase_label: str) -> dict | list | None:
    """Robust JSON parse for LLM output (supports fence stripping + light repair)."""
    payload, _ = _safe_json_loads_with_status(content, phase_label)
    return payload


def _safe_json_loads_with_status(
    content: str,
    phase_label: str,
) -> tuple[dict | list | None, bool]:
    """Robust JSON parse that also reports whether light structural repair was needed."""
    raw_text = (content or "").strip()
    text = _strip_llm_json_payload(raw_text)
    if not text:
        logger.warning("%s: empty response content", phase_label)
        return None, False

    candidates: list[tuple[str, bool]] = [(text, text != raw_text)]

    if raw_text and raw_text != text:
        candidates.append((raw_text, False))

    # Strip markdown fences if present
    if text.startswith("```"):
        stripped = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        if stripped:
            candidates.append((stripped, stripped != text))

    # Extract first JSON object block if extra text surrounds it
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        extracted = text[start : end + 1]
        candidates.append((extracted, extracted != text))

    seen: set[str] = set()
    for candidate, structurally_repaired in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        # Light repair for trailing commas
        normalized = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(normalized), bool(
                structurally_repaired or normalized != candidate
            )
        except json.JSONDecodeError:
            continue

    logger.warning("%s: invalid JSON: %s", phase_label, text[:300])
    return None, False


def _strip_llm_json_payload(content: str) -> str:
    """Strip markdown fences, whitespace, and surrounding chatter from JSON output."""
    text = (content or "").strip()
    if not text:
        return ""

    previous = None
    while text and text != previous:
        previous = text
        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            ).strip()

        start_positions = [
            (text.find("{"), "}"),
            (text.find("["), "]"),
        ]
        start_positions = [
            (pos, end_char) for pos, end_char in start_positions if pos != -1
        ]
        if not start_positions:
            continue

        start, end_char = min(start_positions, key=lambda item: item[0])
        end = text.rfind(end_char)
        if end > start and (start > 0 or end < len(text) - 1):
            text = text[start : end + 1].strip()

    return text.strip()


# ===================================================================
# Dual Extraction comparison
# ===================================================================


def _normalize_for_comparison(val: str | None) -> str:
    """Normalize a cell value for comparison between two extractions."""
    if val is None:
        return ""
    v = unicodedata.normalize("NFKD", val.strip().lower())
    v = "".join(ch for ch in v if not unicodedata.combining(ch))
    # Normalize whitespace
    v = re.sub(r"\s+", " ", v)
    # Normalize common punctuation variants
    v = v.replace("×", "x")
    # Normalize decimal commas where obvious numeric contexts are present.
    if re.fullmatch(r"[\d.,\s+-]+", v):
        v = v.replace(",", ".")
    return v


def _normalize_alnum(val: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_for_comparison(val))


def _normalize_token_set(val: str | None) -> set[str]:
    normalized = _normalize_for_comparison(val)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return {t for t in tokens if t}


def _looks_numeric(val: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", val.strip()))


def _values_equivalent(
    val_a: str | None,
    val_b: str | None,
    *,
    is_critical_column: bool,
) -> bool:
    norm_a = _normalize_for_comparison(val_a)
    norm_b = _normalize_for_comparison(val_b)

    if norm_a == norm_b:
        return True

    if not norm_a or not norm_b:
        return False

    if _looks_numeric(norm_a) and _looks_numeric(norm_b):
        try:
            return abs(float(norm_a) - float(norm_b)) <= 1e-6
        except ValueError:
            return False

    if is_critical_column:
        return False

    alnum_a = _normalize_alnum(norm_a)
    alnum_b = _normalize_alnum(norm_b)

    # Accept separator/punctuation-only variation for sufficiently long strings.
    if alnum_a and alnum_b and len(alnum_a) >= 6 and len(alnum_b) >= 6:
        if alnum_a == alnum_b:
            return True
        if alnum_a in alnum_b or alnum_b in alnum_a:
            return True

    tokens_a = _normalize_token_set(norm_a)
    tokens_b = _normalize_token_set(norm_b)
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
        if overlap >= 0.80:
            return True

    return False


def _collect_position_counts(
    rows: list[dict[str, str | None]],
    columns: list[str],
) -> dict[str, int]:
    """Count every occurrence of each normalized position value in the RAW Vision rows.

    B2/BUG-011: unlike _collect_position_values this function does NOT deduplicate —
    it counts each row individually so duplicate position numbers (e.g. two rows
    both carrying position "10") are each counted. The reconciler uses this to
    detect under-extraction (pdf_count > extracted_count for the same position).

    Returns {} when no position column can be inferred.
    """
    anchor = _infer_anchor_column(columns)
    if not anchor:
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(anchor)
        if value and str(value).strip():
            normalized = " ".join(str(value).strip().upper().split())
            counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _collect_position_values(
    rows: list[dict[str, str | None]],
    columns: list[str],
) -> list[str]:
    """Collect raw position-column values (order-preserving, de-duplicated).

    Used to seed ParsedBOM.raw_pdf_positions on the Vision path. Returns [] when
    no position column can be inferred.
    """
    anchor = _infer_anchor_column(columns)
    if not anchor:
        return []
    seen: set[str] = set()
    values: list[str] = []
    for row in rows:
        value = row.get(anchor)
        if value and str(value).strip():
            normalized = " ".join(str(value).strip().upper().split())
            if normalized not in seen:
                seen.add(normalized)
                values.append(normalized)
    return values


def _text_path_pdf_positions(
    page_texts: list[str],
    rows: list[dict[str, str | None]],
    columns: list[str],
) -> list[str]:
    """PDF-side positions for the reconciler on the text path (ZDL-3).

    Unions the regex-detected positions from the C1-filtered text layer with the
    distinct values of the extracted position column. The position regex
    deliberately excludes bare integers (phantom avoidance), so sequential
    integer positions (1..N) would otherwise be invisible to the reconciler;
    the position column supplies them context-safely. Order-preserving,
    de-duplicated. Lazy import avoids an ingestion→scoring import cycle.
    """
    from src.scoring.ensemble_scorer import _extract_pdf_positions_from_pages

    regex_positions = [
        evidence.position for evidence in _extract_pdf_positions_from_pages(page_texts)
    ]
    column_positions = _collect_position_values(rows, columns)
    return list(dict.fromkeys([*regex_positions, *column_positions]))


def _count_distinct_positions(
    rows: list[dict[str, str | None]],
    columns: list[str],
) -> int:
    """Count distinct position identifiers in the anchor (position) column.

    Used to set ParsedBOM.expected_position_count for the zero-data-loss guard.
    Returns 0 when no position column can be inferred (guard then skipped).
    """
    return len(_collect_position_values(rows, columns))


def _infer_anchor_column(columns: list[str]) -> str | None:
    anchor_keywords = {
        "pos",
        "position",
        "detail",
        "nr",
        "no",
        "num",
        "pozice",
        "index",
    }
    for col in columns:
        col_n = _normalize_for_comparison(col)
        if any(k in col_n for k in anchor_keywords):
            return col
    return None


def _compare_dual_extractions(
    rows_a: list[dict[str, str | None]],
    rows_b: list[dict[str, str | None]],
    columns: list[str],
) -> tuple[dict[int, list[str]], int]:
    """Compare two extractions using sequence alignment. Return (mismatch_flags, delta).

    BUG-016: the old index-/anchor-based pairing drifted when extraction B dropped a
    middle row — all subsequent rows were matched to wrong partners, producing false
    mismatches on correct rows and missing the actual drop. Replaced with
    difflib.SequenceMatcher on per-row key sequences so aligned pairs are always
    correct and unmatched rows from A are reliably flagged as MISSING_ROW.

    Row key: anchor column value (alnum-normalised) when available, otherwise the
    concatenation of the first two non-empty cell values — providing a content
    fingerprint that survives minor OCR variation between runs A and B.

    Returns:
        mismatches  — {row_index_in_A: [flag_strings]}
        row_count_delta — len(rows_a) - len(rows_b); != 0 warns about row count drift
    """
    mismatches: dict[int, list[str]] = {}

    # Identify critical columns for extra-strict comparison.
    qty_keywords = {
        "stk",
        "stück",
        "stck",
        "qty",
        "quantity",
        "anzahl",
        "menge",
        "množství",
        "pcs",
        "count",
        "design",
    }
    dim_keywords = {
        "fertigma",
        "fertigmaß",
        "dimension",
        "maße",
        "masse",
        "rohmass",
        "rohma",
        "abmaß",
        "rozměr",
        "čistý",
    }
    critical_columns = {
        col
        for col in columns
        if any(kw in col.lower() for kw in qty_keywords | dim_keywords)
    }
    anchor_col = _infer_anchor_column(columns)
    if anchor_col:
        critical_columns.add(anchor_col)

    def _row_key(row: dict[str, str | None]) -> str:
        """Stable per-row comparison key for SequenceMatcher."""
        if anchor_col:
            anchor_val = _normalize_alnum(row.get(anchor_col))
            if anchor_val:
                return anchor_val
        # Fallback: first two non-empty normalized cell values concatenated.
        parts: list[str] = []
        for col in columns:
            v = _normalize_alnum(row.get(col))
            if v:
                parts.append(v)
            if len(parts) >= 2:
                break
        return "".join(parts)

    keys_a = [_row_key(r) for r in rows_a]
    keys_b = [_row_key(r) for r in rows_b]

    # Use SequenceMatcher to find aligned blocks of matching keys.
    # autojunk=False: never treat high-frequency keys as junk — position numbers
    # like "1" appear in every BOM but are the most important anchor.
    matcher = difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)

    matched_a: set[int] = set()
    matched_b: set[int] = set()

    for block in matcher.get_matching_blocks():
        alo, blo, size = block.a, block.b, block.size
        for offset in range(size):
            idx_a = alo + offset
            idx_b = blo + offset
            row_a = rows_a[idx_a]
            row_b = rows_b[idx_b]
            matched_a.add(idx_a)
            matched_b.add(idx_b)

            flags: list[str] = []
            for col in columns:
                val_a = row_a.get(col)
                val_b = row_b.get(col)
                norm_a = _normalize_for_comparison(val_a)
                norm_b = _normalize_for_comparison(val_b)
                values_equal = _values_equivalent(
                    val_a, val_b, is_critical_column=(col in critical_columns)
                )
                if not values_equal:
                    if norm_a and norm_b:
                        flags.append(f"{col}: dual_mismatch (A='{val_a}', B='{val_b}')")
                    elif col in critical_columns:
                        flags.append(f"{col}: dual_mismatch (A='{val_a}', B='{val_b}')")
            if flags:
                mismatches[idx_a] = flags

    # Rows from A that have no aligned partner in B → flag all critical columns.
    for idx_a, row_a in enumerate(rows_a):
        if idx_a in matched_a:
            continue
        flags = []
        for col in columns:
            val_a = row_a.get(col)
            if val_a is not None and col in critical_columns:
                flags.append(f"{col}: dual_mismatch (A='{val_a}', B=MISSING_ROW)")
        if flags:
            mismatches[idx_a] = flags

    row_count_delta = len(rows_a) - len(rows_b)
    total = sum(len(f) for f in mismatches.values())
    if total:
        logger.warning(
            "Dual Extraction: %d mismatches across %d rows",
            total,
            len(mismatches),
        )
    else:
        logger.info("Dual Extraction: all values match — high confidence")

    return mismatches, row_count_delta


# ===================================================================
# Deduplication
# ===================================================================


def _deduplicate_rows(
    rows: list[dict[str, str | None]],
    columns: list[str],
) -> list[dict[str, str | None]]:
    """Remove only FULL duplicates — rows identical across ALL columns.

    Previously the dedup key was just the first 3 columns, which silently
    dropped distinct rows that happened to share those columns (e.g. per-page
    reset positions, identical descriptions with different dimensions). This is
    a data-loss vector for large multi-page BOMs.

    Now the key spans every column, so only genuine full duplicates (typically
    repeated header rows that survived earlier filtering) are removed. Each
    actual removal is logged at WARNING level so silent loss is impossible.
    """
    if not rows:
        return rows

    # Key over the union of all keys (detected columns + any extra row keys),
    # in a stable order, so the comparison is over the COMPLETE row.
    key_columns: list[str] = list(columns)
    for row in rows:
        for key in row:
            if key not in key_columns:
                key_columns.append(key)

    seen: set[tuple[str | None, ...]] = set()
    unique: list[dict[str, str | None]] = []

    for row_index, row in enumerate(rows):
        key = tuple(row.get(c) for c in key_columns)
        if key in seen:
            logger.warning(
                "Deduplication: dropped row %d as an exact full-row duplicate: %s",
                row_index,
                {c: row.get(c) for c in key_columns if row.get(c) is not None},
            )
            continue
        seen.add(key)
        unique.append(row)

    return unique


# ===================================================================
# Post-validation — plausibility checks per cell
# ===================================================================

# Semantic column type mapping: detected header → logical type
# Columns that should NOT be type-mapped even if they match a keyword
_COLUMN_TYPE_EXCLUSIONS: set[str] = {
    "artikelnr",
    "artikelnummer",
    "sachnummer",
    "sachnr",
    "part no",
    "teilename",
    "teil",
    "teilenr",
    "bauteilnr",
}

_COLUMN_TYPE_KEYWORDS: dict[str, list[str]] = {
    "position": [
        "pos",
        "position",
        "no",
        "num",
        "detail",
        "pozice",
    ],
    "quantity": [
        "stk",
        "stück",
        "stck",
        "qty",
        "quantity",
        "anzahl",
        "menge",
        "množství",
        "pcs",
        "count",
    ],
    "material": [
        "werkst",
        "werkstoff",
        "material",
        "matériau",
        "materiale",
        "norma",
    ],
    "dimensions": [
        "fertigma",
        "fertigmaß",
        "dimension",
        "maße",
        "masse",
        "rohmass",
        "rohma",
        "finish",
        "abmaß",
        "rozměr",
    ],
    "hardness": [
        "härte",
        "hardness",
        "hrc",
        "hrb",
        "durezza",
        "tepelzprac",
    ],
    "description": [
        "benennung",
        "bezeichnung",
        "description",
        "popis",
        "name",
        "teilename",
        "teil",
        "denominazione",
    ],
}

_VALIDATION_RULES: dict[str, dict] = {
    "position": {
        "type": "integer",
        "min": 1,
        "max": 9999,
        "max_length": 4,
        "flag_if_contains": ["-", "x", ".", "Ø", "_", "/"],
    },
    "quantity": {
        "type": "integer",
        "min": 1,
        "max": 999,
        "flag_if_contains": ["-", "x", ".", "Ø", "_"],
    },
    "material": {
        "type": "string",
        "expected_patterns": [
            r"^1[\.\-]\d{4}",  # Werkstoffnummer (1.2343, 1-2343)
            r"^[A-Z]{1,4}\d",  # Material abbrev (Cu, St52, GG20, AlSi)
            r"^(blank|VGN|VGT|DIN)",  # Special treatments / norms
            r"^[A-Za-z]{2,}",  # General text (at least 2 letters)
        ],
        "flag_if_contains": [" x ", "×", "HRC", "HB"],
    },
    "dimensions": {
        "type": "string",
        "expected_patterns": [
            r"\d+[.,]?\d*\s*[xX×\*]\s*\d+",  # Standard dimensions
            r"[ØD]\s*\d+",  # Diameter
            r"^\d+[.,]\d+$",  # Single number
        ],
        "flag_if_contains": ["ESU", "HRC", "VGN"],
        "dimension_consistency": True,
    },
    "hardness": {
        "type": "string",
        "expected_patterns": [
            r"\d+.*HRC",
            r"\d+.*HB",
            r"\d+.*N/mm",
            r"\d+\s*±",
            r"^\d+[.,]?\d*$",  # Plain number (European format)
            r"^\d+\s*-\s*\d+",  # Range like "48 - 52"
        ],
    },
}


def _infer_column_types(columns: list[str]) -> dict[str, str]:
    """Map detected column names to semantic types (position, quantity, etc.)."""
    mapping: dict[str, str] = {}
    for col in columns:
        col_lower = col.lower().strip().rstrip(".")
        # Skip columns on the exclusion list
        if col_lower in _COLUMN_TYPE_EXCLUSIONS:
            continue
        for sem_type, keywords in _COLUMN_TYPE_KEYWORDS.items():
            if any(kw in col_lower for kw in keywords):
                mapping[col] = sem_type
                break
    return mapping


def _post_validate_extraction(
    rows: list[dict[str, str | None]],
    detected_columns: list[str],
) -> tuple[list[dict[str, str | None]], dict[int, list[str]]]:
    """Validate each extracted cell against plausibility rules.

    Returns the (cleaned) rows and a dict of {row_index: [flag_strings]}.
    Flagged cells are capped at YELLOW in the ensemble scorer (PLAUS soft veto).
    """
    column_type_mapping = _infer_column_types(detected_columns)
    row_flags: dict[int, list[str]] = {}

    for idx, row in enumerate(rows):
        flags: list[str] = []
        for col_name, value in row.items():
            if col_name.startswith("_") or value is None:
                continue
            col_type = column_type_mapping.get(col_name)
            if col_type and col_type in _VALIDATION_RULES:
                cell_flags = _validate_cell(value, _VALIDATION_RULES[col_type])
                flags.extend(f"PLAUS:{col_name}: {f}" for f in cell_flags)

        if flags:
            row_flags[idx] = flags

    return rows, row_flags


def _validate_cell(value: str, rules: dict) -> list[str]:
    """Check a single cell value against rules. Returns list of flag strings."""
    flags: list[str] = []
    val = value.strip()
    if not val:
        return flags

    expected_type = rules.get("type", "string")

    # Integer type check
    if expected_type == "integer":
        try:
            num = int(val)
            if "min" in rules and num < rules["min"]:
                flags.append(f"value {num} below minimum {rules['min']}")
            if "max" in rules and num > rules["max"]:
                flags.append(f"value {num} above maximum {rules['max']}")
        except ValueError:
            flags.append(f"expected integer, got '{val}'")

        max_len = rules.get("max_length")
        if max_len and len(val) > max_len:
            flags.append(f"length {len(val)} exceeds {max_len}")

    # Flag if contains unexpected characters
    for forbidden in rules.get("flag_if_contains", []):
        if forbidden in val:
            flags.append(f"contains '{forbidden}' — possible column bleeding")
            break  # One flag is enough

    # Pattern check for string types
    if expected_type == "string" and "expected_patterns" in rules:
        patterns = rules["expected_patterns"]
        if patterns and not any(re.search(p, val) for p in patterns):
            flags.append(f"value '{val[:30]}' does not match expected patterns")

    # Dimension consistency check: if a dimension contains "x" (like 761x656x290),
    # check that the digit counts of components are consistent.
    # e.g., "76x165x290" is suspicious because 2-digit next to 3-digit values.
    if rules.get("dimension_consistency"):
        dim_parts = re.split(r"[xX×\*]", val)
        numeric_parts = [p.strip().replace(",", ".") for p in dim_parts if p.strip()]
        digit_lengths = []
        for p in numeric_parts:
            m = re.match(r"(\d+)", p)
            if m:
                digit_lengths.append(len(m.group(1)))
        if len(digit_lengths) >= 2:
            max_digits = max(digit_lengths)
            min_digits = min(digit_lengths)
            # Flag if: digit count difference >= 2
            # OR: one component has 1-2 digits while another has 3+ digits
            #     AND the majority of components have 3+ digits
            count_3plus = sum(1 for d in digit_lengths if d >= 3)
            suspicious = False
            if max_digits - min_digits >= 2:
                suspicious = True
            elif max_digits >= 3 and min_digits <= 2 and count_3plus >= 2:
                # e.g. [2, 3, 3] — one outlier among larger values
                suspicious = True

            if suspicious:
                flags.append(
                    f"dimension digit count inconsistent ({digit_lengths}) "
                    f"— possible OCR misread in '{val}'"
                )

    return flags


# ===================================================================
# Coordinate-Based Text-Layer Verification (Deterministic Anchor)
# ===================================================================

# Tolerance for grouping words into the same row (PDF points)
_Y_ROW_TOLERANCE = 5.0

# Fuzzy spatial thresholds for row-locked validation
_Y_IOU_CONFIRMED = 0.55
_Y_IOU_ROW_CORRIDOR = 0.35
_Y_OFFSET_TOLERANCE = 12.0


def _cross_check_with_text_layer(
    filepath: Path,
    rows: list[dict[str, str | None]],
    detected_columns: list[str],
) -> tuple[
    dict[int, list[str]],
    dict[int, list[str]],
    dict[int, list[str]],
    dict[int, list[str]],
    dict[int, list[str]],
    dict[int, dict[str, dict[str, object]]],
    bool,
]:
    """Verify extracted values against PDF text layer using row anchors + y-IoU.

    Outputs three evidence channels:
      - mismatches: hard contradictions (COORDMISS)
      - confirmations: strong row-locked confirmations (COORDOK)
      - row_corridors: weaker row-anchored confirmations (COORDROW)
            - column_conflicts: value found in wrong X-column corridor (COORDCOL)
            - detail_proofs: normalized Detail Number proof in matched row (COORDDET)
    """
    doc = open_pdf_document(filepath)

    all_words: list[tuple[int, float, float, float, float, str]] = []
    full_text_len = 0
    for page_idx, page in enumerate(doc):
        page_words = page.get_text("words")
        # words: (x0, y0, x1, y1, word, block_no, line_no, word_no)
        for w in page_words:
            token = str(w[4]).strip()
            if not token:
                continue
            all_words.append((page_idx, w[0], w[1], w[2], w[3], token))
            full_text_len += len(token)
    doc.close()

    if full_text_len < 50:
        logger.info("Coordinate check: skipped (scanned PDF or minimal text layer)")
        return {}, {}, {}, {}, {}, {}, False

    spatial_rows = _build_spatial_rows(all_words)
    if len(spatial_rows) < 2:
        logger.info("Coordinate check: too few spatial rows (%d)", len(spatial_rows))
        return {}, {}, {}, {}, {}, {}, True

    logger.info(
        "Coordinate check: %d words -> %d spatial rows",
        len(all_words),
        len(spatial_rows),
    )

    column_types = _infer_column_types(detected_columns)
    column_profile = _build_column_profile(spatial_rows, detected_columns)
    page_rows = _group_rows_by_page(spatial_rows)

    mismatches: dict[int, list[str]] = {}
    confirmations: dict[int, list[str]] = {}
    row_corridors: dict[int, list[str]] = {}
    column_conflicts: dict[int, list[str]] = {}
    detail_proofs: dict[int, list[str]] = {}
    source_locations: dict[int, dict[str, dict[str, object]]] = {}

    for idx, vision_row in enumerate(rows):
        matched_row, anchor_score = _find_matching_spatial_row(
            vision_row,
            detected_columns,
            column_types,
            spatial_rows,
        )
        if matched_row is None:
            continue

        merged_row = _merge_multiline_segment(
            matched_row,
            vision_row,
            detected_columns,
            column_types,
            page_rows,
            column_profile,
        )

        row_mismatches: set[str] = set()
        row_confirmed: set[str] = set()
        row_corridor_hits: set[str] = set()
        row_col_conflicts: set[str] = set()
        row_detail_proofs: set[str] = set()
        row_source_locations: dict[str, dict[str, object]] = {}

        for col in detected_columns:
            vision_val = (vision_row.get(col) or "").strip()
            if not vision_val:
                continue

            row_source_locations[col] = _build_source_location(
                merged_row,
                column_profile.get(col),
            )

            col_type = column_types.get(col)
            if not _is_verifiable_column(vision_val, col_type):
                continue

            if col_type == "position":
                if _detail_number_row_match(vision_val, str(merged_row["text"])):
                    # Detail Number is the row anchor: content proof in the matched
                    # Y-corridor is sufficient even with minor X-offset noise.
                    row_detail_proofs.add(col)
                    row_confirmed.add(col)
                    row_corridor_hits.add(col)
                    continue

            value_tokens = _extract_verification_tokens(vision_val, col_type)
            if not value_tokens:
                continue

            token_statuses: list[str] = []
            for token in value_tokens:
                expected_x = column_profile.get(col)
                status, matched_token, y_iou = _find_best_token_match(
                    token,
                    merged_row,
                    expected_x,
                )
                token_statuses.append(status)

                if status == "mismatch" and matched_token:
                    row_mismatches.add(
                        f"{col}: Vision='{token}' vs PDF='{matched_token}'"
                    )

                if status == "corridor" and anchor_score >= 2:
                    row_corridor_hits.add(col)

                if status == "confirm" and y_iou >= _Y_IOU_CONFIRMED:
                    row_confirmed.add(col)

                if status == "column_conflict":
                    row_col_conflicts.add(col)

            if col in row_col_conflicts:
                continue

            if token_statuses and all(s == "confirm" for s in token_statuses):
                row_confirmed.add(col)
            elif (
                token_statuses
                and all(s in {"confirm", "corridor"} for s in token_statuses)
                and anchor_score >= 2
            ):
                row_corridor_hits.add(col)

        if row_mismatches:
            mismatches[idx] = sorted(row_mismatches)
        if row_confirmed:
            confirmations[idx] = sorted(row_confirmed)
        if row_corridor_hits:
            row_corridors[idx] = sorted(row_corridor_hits)
        if row_col_conflicts:
            column_conflicts[idx] = sorted(
                f"{col}: Spalten-Konflikt erkannt" for col in row_col_conflicts
            )
        if row_detail_proofs:
            detail_proofs[idx] = sorted(
                f"{col}: detail_number_normalized_match" for col in row_detail_proofs
            )
        if row_source_locations:
            source_locations[idx] = row_source_locations

    total_m = sum(len(f) for f in mismatches.values())
    total_c = sum(len(f) for f in confirmations.values())
    total_r = sum(len(f) for f in row_corridors.values())
    total_x = sum(len(f) for f in column_conflicts.values())
    total_d = sum(len(f) for f in detail_proofs.values())
    logger.info(
        "Coordinate check: %d mismatches, %d confirmed, %d row-corridor, %d column-conflicts, %d detail-proofs across %d rows",
        total_m,
        total_c,
        total_r,
        total_x,
        total_d,
        len(rows),
    )

    return (
        mismatches,
        confirmations,
        row_corridors,
        column_conflicts,
        detail_proofs,
        source_locations,
        True,
    )


def _build_source_location(
    spatial_row: dict[str, object],
    expected_x_range: tuple[float, float] | None,
) -> dict[str, object]:
    """Build an approximate source-location bbox for one extracted cell."""
    page_number = int(spatial_row["page"]) + 1
    row_y0 = float(spatial_row["y_min"])
    row_y1 = float(spatial_row["y_max"])

    if expected_x_range is None:
        return {
            "page": page_number,
            "bbox": [
                float(spatial_row["x_min"]),
                row_y0,
                float(spatial_row["x_max"]),
                row_y1,
            ],
            "text": str(spatial_row["text"]),
            "match_type": "row_fallback",
        }

    corridor_words = [
        word
        for word in spatial_row["words"]
        if _x_in_corridor((word[0] + word[2]) / 2, expected_x_range)
    ]
    if corridor_words:
        return {
            "page": page_number,
            "bbox": [
                min(word[0] for word in corridor_words),
                min(word[1] for word in corridor_words),
                max(word[2] for word in corridor_words),
                max(word[3] for word in corridor_words),
            ],
            "text": " ".join(word[4] for word in corridor_words),
            "match_type": "column_corridor",
        }

    return {
        "page": page_number,
        "bbox": [expected_x_range[0], row_y0, expected_x_range[1], row_y1],
        "text": "",
        "match_type": "column_estimate",
    }


def _build_spatial_rows(
    words: list[tuple[int, float, float, float, float, str]],
) -> list[dict[str, object]]:
    """Cluster words into page-local spatial rows by y coordinate."""
    from collections import defaultdict

    buckets: dict[tuple[int, int], list[tuple[float, float, float, float, str]]] = (
        defaultdict(list)
    )
    for page_idx, x0, y0, x1, y1, text in words:
        y_center = (y0 + y1) / 2
        y_key = round(y_center / _Y_ROW_TOLERANCE)
        buckets[(page_idx, y_key)].append((x0, y0, x1, y1, text))

    rows: list[dict[str, object]] = []
    for (page_idx, _), row_words in sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        sorted_words = sorted(row_words, key=lambda w: w[0])
        text = " ".join(w[4] for w in sorted_words)
        y_min = min(w[1] for w in sorted_words)
        y_max = max(w[3] for w in sorted_words)
        x_min = min(w[0] for w in sorted_words)
        x_max = max(w[2] for w in sorted_words)

        rows.append(
            {
                "row_id": len(rows),
                "page": page_idx,
                "text": text,
                "text_lower": text.lower(),
                "tokens": {_normalize_token(w[4]) for w in sorted_words},
                "words": sorted_words,
                "y_min": y_min,
                "y_max": y_max,
                "x_min": x_min,
                "x_max": x_max,
            }
        )

    return rows


def _group_rows_by_page(
    spatial_rows: list[dict[str, object]],
) -> dict[int, list[dict[str, object]]]:
    """Group and sort spatial rows per page by y coordinate."""
    grouped: dict[int, list[dict[str, object]]] = {}
    for row in spatial_rows:
        page = int(row["page"])
        grouped.setdefault(page, []).append(row)

    for rows_on_page in grouped.values():
        rows_on_page.sort(key=lambda r: float(r["y_min"]))

    return grouped


def _build_column_profile(
    spatial_rows: list[dict[str, object]],
    columns: list[str],
) -> dict[str, tuple[float, float]]:
    """Infer X corridors per detected column from header text positions."""
    if not columns:
        return {}

    best_row: dict[str, object] | None = None
    best_score = 0
    for row in spatial_rows[:120]:
        row_text = str(row["text_lower"])
        score = 0
        for col in columns:
            for token in _header_token_variants(col):
                if token and token in row_text:
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None or best_score < 2:
        return {}

    words = list(best_row["words"])
    centers: dict[str, float] = {}
    prev_center = -1.0

    for col in columns:
        variants = _header_token_variants(col)
        candidate_centers: list[float] = []
        for x0, _y0, x1, _y1, text in words:
            wnorm = _normalize_token(text)
            if not wnorm:
                continue
            if any(v in wnorm or wnorm in v for v in variants):
                center = (x0 + x1) / 2
                if center >= prev_center - 2:
                    candidate_centers.append(center)

        if not candidate_centers:
            continue

        chosen = min(candidate_centers)
        centers[col] = chosen
        prev_center = chosen

    if len(centers) < 2:
        return {}

    ordered = [(c, centers[c]) for c in columns if c in centers]
    gaps = [ordered[i + 1][1] - ordered[i][1] for i in range(len(ordered) - 1)]
    avg_gap = sum(gaps) / len(gaps) if gaps else 60.0

    profile: dict[str, tuple[float, float]] = {}
    for idx, (col, center) in enumerate(ordered):
        if idx == 0:
            left = center - avg_gap / 2
        else:
            left = (ordered[idx - 1][1] + center) / 2

        if idx == len(ordered) - 1:
            right = center + avg_gap / 2
        else:
            right = (center + ordered[idx + 1][1]) / 2

        profile[col] = (left, right)

    return profile


def _header_token_variants(column_name: str) -> set[str]:
    """Generate robust header tokens for matching OCR header rows."""
    base = _normalize_token(column_name)
    variants = {base}
    for part in re.split(r"[^A-Za-z0-9]+", column_name):
        p = _normalize_token(part)
        if len(p) >= 2:
            variants.add(p)
    return {v for v in variants if v}


def _merge_multiline_segment(
    anchor_row: dict[str, object],
    vision_row: dict[str, str | None],
    detected_columns: list[str],
    column_types: dict[str, str],
    page_rows: dict[int, list[dict[str, object]]],
    column_profile: dict[str, tuple[float, float]],
) -> dict[str, object]:
    """Expand one matched row downward until the next position anchor appears."""
    position_col = next(
        (col for col in detected_columns if column_types.get(col) == "position"),
        None,
    )
    if not position_col:
        return anchor_row

    pos_value = (vision_row.get(position_col) or "").strip()
    m = re.search(r"\d{1,4}", pos_value)
    if not m:
        return anchor_row
    current_pos = m.group(0)

    page = int(anchor_row["page"])
    rows_on_page = page_rows.get(page, [])
    if not rows_on_page:
        return anchor_row

    start_idx = next(
        (
            idx
            for idx, row in enumerate(rows_on_page)
            if int(row["row_id"]) == int(anchor_row["row_id"])
        ),
        -1,
    )
    if start_idx < 0:
        return anchor_row

    pos_x = column_profile.get(position_col)

    merged_rows = [rows_on_page[start_idx]]
    for idx in range(start_idx + 1, min(len(rows_on_page), start_idx + 8)):
        candidate = rows_on_page[idx]
        if _row_contains_new_position(candidate, current_pos, pos_x):
            break
        merged_rows.append(candidate)

    if len(merged_rows) == 1:
        return anchor_row

    merged_words: list[tuple[float, float, float, float, str]] = []
    merged_tokens: set[str] = set()
    text_parts: list[str] = []
    y_min = float(merged_rows[0]["y_min"])
    y_max = float(merged_rows[0]["y_max"])
    x_min = float(merged_rows[0]["x_min"])
    x_max = float(merged_rows[0]["x_max"])

    for row in merged_rows:
        merged_words.extend(row["words"])
        merged_tokens.update(row["tokens"])
        text_parts.append(str(row["text"]))
        y_min = min(y_min, float(row["y_min"]))
        y_max = max(y_max, float(row["y_max"]))
        x_min = min(x_min, float(row["x_min"]))
        x_max = max(x_max, float(row["x_max"]))

    return {
        "row_id": int(anchor_row["row_id"]),
        "page": page,
        "text": " ".join(text_parts),
        "text_lower": " ".join(text_parts).lower(),
        "tokens": merged_tokens,
        "words": merged_words,
        "y_min": y_min,
        "y_max": y_max,
        "x_min": x_min,
        "x_max": x_max,
    }


def _row_contains_new_position(
    row: dict[str, object],
    current_pos: str,
    pos_x_range: tuple[float, float] | None,
) -> bool:
    """Detect if a row starts a new position number in the position corridor."""
    for x0, _y0, x1, _y1, text in row["words"]:
        token = text.strip()
        if not re.fullmatch(r"\d{1,4}", token):
            continue
        if token == current_pos:
            continue

        center = (x0 + x1) / 2
        if pos_x_range is not None and not _x_in_corridor(center, pos_x_range):
            continue
        return True
    return False


def _find_matching_spatial_row(
    vision_row: dict[str, str | None],
    columns: list[str],
    column_types: dict[str, str],
    spatial_rows: list[dict[str, object]],
) -> tuple[dict[str, object] | None, int]:
    """Find best spatial row using semantic row anchors."""
    anchor_tokens: set[str] = set()
    for col in columns:
        value = (vision_row.get(col) or "").strip()
        if not value:
            continue

        col_type = column_types.get(col)
        if col_type in {"description", "position", "quantity", "material"}:
            for token in _extract_verification_tokens(value, col_type):
                norm = _normalize_token(token)
                if len(norm) >= 2:
                    anchor_tokens.add(norm)

    if not anchor_tokens:
        return None, 0

    best_row: dict[str, object] | None = None
    best_score = 0

    for spatial_row in spatial_rows:
        row_tokens = spatial_row["tokens"]
        row_text = spatial_row["text_lower"]
        score = 0
        for token in anchor_tokens:
            if token in row_tokens or token in row_text:
                score += 1

        if score > best_score:
            best_score = score
            best_row = spatial_row

    if best_score >= 2:
        return best_row, best_score
    return None, best_score


def _find_best_token_match(
    token: str,
    spatial_row: dict[str, object],
    expected_x_range: tuple[float, float] | None,
) -> tuple[str, str | None, float]:
    """Resolve one extracted token against one spatial row.

    Returns (status, matched_token, y_iou):
      - confirm: exact/normalized hit with strong y-overlap
      - corridor: weak spatial confirmation (same row corridor)
      - mismatch: similar but conflicting value in same row
    - column_conflict: value exists, but outside expected X corridor
      - none: no useful evidence
    """
    norm_token = _normalize_token(token)
    if not norm_token:
        return "none", None, 0.0

    words = spatial_row["words"]
    row_y0 = float(spatial_row["y_min"])
    row_y1 = float(spatial_row["y_max"])

    best_exact_token: str | None = None
    best_exact_iou = -1.0
    best_exact_y0: float | None = None
    best_exact_y1: float | None = None
    best_exact_out_x_token: str | None = None
    best_exact_out_x_iou = -1.0
    best_similar_token: str | None = None
    best_similar_iou = -1.0

    # BUG-010: numeric tokens compare on their canonical decimal form —
    # _normalize_token strips punctuation and collapses "4.5"/"45" and
    # "1-2"/"12" into the same string, producing false COORDOK confirmations.
    token_numeric = _normalize_numeric_token(token)

    for _x0, y0, _x1, y1, word_text in words:
        norm_word = _normalize_token(word_text)
        if not norm_word:
            continue

        y_iou = _axis_iou(y0, y1, row_y0, row_y1)
        in_x = expected_x_range is None or _x_in_corridor(
            (_x0 + _x1) / 2,
            expected_x_range,
        )

        word_numeric = _normalize_numeric_token(word_text)
        if token_numeric is not None or word_numeric is not None:
            # Numeric-aware path: an exact hit requires the SAME canonical
            # decimal form ("4,5" == "4.5"; "4.5" != "45"; "12" != "1-2").
            # A shape mismatch (one side numeric, one not) is never exact.
            is_exact = (
                token_numeric is not None
                and word_numeric is not None
                and token_numeric == word_numeric
            )
            if is_exact:
                if in_x:
                    if y_iou > best_exact_iou:
                        best_exact_token = word_text
                        best_exact_iou = y_iou
                        best_exact_y0 = y0
                        best_exact_y1 = y1
                elif y_iou > best_exact_out_x_iou:
                    best_exact_out_x_token = word_text
                    best_exact_out_x_iou = y_iou
            elif _is_close_numeric_miss(norm_token, norm_word) and in_x:
                if y_iou > best_similar_iou:
                    best_similar_token = word_text
                    best_similar_iou = y_iou
            continue

        if norm_word in _token_variants(norm_token):
            if in_x:
                if y_iou > best_exact_iou:
                    best_exact_token = word_text
                    best_exact_iou = y_iou
                    best_exact_y0 = y0
                    best_exact_y1 = y1
            elif y_iou > best_exact_out_x_iou:
                best_exact_out_x_token = word_text
                best_exact_out_x_iou = y_iou
            continue

        if _is_close_numeric_miss(norm_token, norm_word) and in_x:
            if y_iou > best_similar_iou:
                best_similar_token = word_text
                best_similar_iou = y_iou

    if best_exact_token is not None:
        if best_exact_iou >= _Y_IOU_CONFIRMED:
            return "confirm", best_exact_token, best_exact_iou
        if best_exact_iou >= _Y_IOU_ROW_CORRIDOR:
            return "corridor", best_exact_token, best_exact_iou
        if (
            best_exact_y0 is not None
            and best_exact_y1 is not None
            and _vertical_gap(best_exact_y0, best_exact_y1, row_y0, row_y1)
            <= _Y_OFFSET_TOLERANCE
        ):
            return "corridor", best_exact_token, best_exact_iou
        return "none", best_exact_token, best_exact_iou

    if best_similar_token is not None and best_similar_iou >= _Y_IOU_ROW_CORRIDOR:
        return "mismatch", best_similar_token, best_similar_iou

    if best_exact_out_x_token is not None:
        return "column_conflict", best_exact_out_x_token, best_exact_out_x_iou

    return "none", None, 0.0


def _x_in_corridor(x_center: float, x_range: tuple[float, float]) -> bool:
    """Check if x-center lies within the expected column corridor."""
    left, right = x_range
    pad = 4.0
    return (left - pad) <= x_center <= (right + pad)


def _is_verifiable_column(value: str, col_type: str | None) -> bool:
    """Return True for columns that should participate in coordinate checks."""
    if col_type in {"position", "quantity", "dimensions", "material"}:
        return True
    return bool(re.search(r"\d", value))


def _extract_verification_tokens(value: str, col_type: str | None) -> list[str]:
    """Extract comparable tokens for row-locked verification."""
    text = value.strip()
    if not text:
        return []

    if col_type in {"position", "quantity", "dimensions"}:
        nums = re.findall(r"\d+(?:[.,]\d+)?", text)
        return nums if nums else [text]

    if col_type == "material":
        parts = re.findall(r"[A-Za-z0-9.\-]+", text)
        tokens = [p for p in parts if len(p) >= 2]
        return tokens if tokens else [text]

    parts = re.findall(r"[A-Za-z0-9.\-]+", text)
    return [p for p in parts if len(p) >= 2] or [text]


def _normalize_token(value: str) -> str:
    """Normalize token for robust equality checks."""
    token = value.strip().lower()
    # Normalize exactly as requested: keep only letters and digits.
    token = re.sub(r"[^a-z0-9]", "", token)
    return token


_NUMERIC_TOKEN_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")


def _normalize_numeric_token(value: str) -> str | None:
    """Normalize a numeric token for decimal-separator-agnostic comparison.

    Returns the canonical form (comma replaced by dot) if the raw token
    (after strip) matches a plain numeric pattern, otherwise None.

    Examples:
      "4,5"  → "4.5"
      "45"   → "45"
      "4.5"  → "4.5"
      "4mm"  → None   (mixed — not purely numeric)
    """
    raw = unicodedata.normalize("NFKC", value.strip())
    if not _NUMERIC_TOKEN_RE.match(raw):
        return None
    return raw.replace(",", ".")


def _detail_number_row_match(vision_value: str, row_text: str) -> bool:
    """Relaxed content proof for Detail Number inside matched Y-row corridor.

    Rules:
    - Ignore separators like '_', '-', '.', and spaces.
    - Accept direct normalized containment.
    - Accept digit-sequence containment.
    - Accept part-wise substring proof for segmented keys.
    """
    if not vision_value or not row_text:
        return False

    norm_v = re.sub(r"[_\-\.\s]", "", vision_value.lower())
    norm_r = re.sub(r"[_\-\.\s]", "", row_text.lower())
    if norm_v and norm_v in norm_r:
        return True

    digits_v = re.sub(r"\D", "", vision_value)
    digits_r = re.sub(r"\D", "", row_text)
    if digits_v and digits_v in digits_r:
        return True

    parts = [
        re.sub(r"[_\-\.\s]", "", p.lower())
        for p in re.split(r"[_\-\.\s]+", vision_value)
        if p.strip()
    ]
    parts = [p for p in parts if p]
    if len(parts) >= 2 and all(p in norm_r for p in parts):
        return True

    return False


def _token_variants(token: str) -> set[str]:
    """Build decimal-separator and punctuation variants for matching."""
    return {token}


def _vertical_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Vertical gap between two y-intervals (0 when they overlap)."""
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0.0


def _axis_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    """1D IoU (used on y-axis corridor overlap)."""
    lo = max(a0, b0)
    hi = min(a1, b1)
    intersection = max(0.0, hi - lo)
    union = max(a1, b1) - min(a0, b0)
    if union <= 0:
        return 0.0
    return intersection / union


def _is_close_numeric_miss(left: str, right: str) -> bool:
    """Detect likely OCR near-miss for numeric tokens."""
    if left == right:
        return False

    # Restrict fuzzy numeric mismatches to pure-digit tokens.
    # Mixed alnum tokens (e.g., "4mm") should not be treated as a numeric miss.
    if not re.fullmatch(r"\d+", left) or not re.fullmatch(r"\d+", right):
        return False

    left_num = re.sub(r"[^0-9]", "", left)
    right_num = re.sub(r"[^0-9]", "", right)
    if not left_num or not right_num:
        return False

    if left_num.startswith(right_num) and 0 < len(left_num) - len(right_num) <= 2:
        return True
    if right_num.startswith(left_num) and 0 < len(right_num) - len(left_num) <= 2:
        return True

    if len(left_num) == len(right_num):
        return _levenshtein_distance(left_num, right_num) <= 1

    return _levenshtein_distance(left_num, right_num) <= 1


def _levenshtein_distance(left: str, right: str) -> int:
    """Compute Levenshtein distance for short OCR near-miss detection."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    prev = list(range(len(right) + 1))
    for i, lch in enumerate(left, start=1):
        curr = [i]
        for j, rch in enumerate(right, start=1):
            insert_cost = curr[j - 1] + 1
            delete_cost = prev[j] + 1
            sub_cost = prev[j - 1] + (0 if lch == rch else 1)
            curr.append(min(insert_cost, delete_cost, sub_cost))
        prev = curr
    return prev[-1]
