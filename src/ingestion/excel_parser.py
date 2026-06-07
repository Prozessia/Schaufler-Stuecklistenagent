"""Excel Parser — parse .xlsx/.xls files into ParsedBOM."""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell

from src.core.models import (
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceMetadata,
)
from src.ingestion.file_router import infer_customer

logger = logging.getLogger(__name__)


def parse_excel(
    filepath: Path | str,
    sheet_name: str | None = None,
    header_row: int | None = None,
    data_start_row: int | None = None,
) -> ParsedBOM:
    """Parse an Excel file into a ParsedBOM.

    Args:
        filepath: Path to the Excel file.
        sheet_name: Sheet to parse. Defaults to first sheet, or "Stückliste" if it exists.
        header_row: 1-based row number for headers. Auto-detected if None.
        data_start_row: 1-based row number where data starts. Auto-detected if None.
    """
    filepath = Path(filepath)
    wb = openpyxl.load_workbook(filepath, data_only=True)

    # Select sheet
    ws = _select_sheet(wb, sheet_name)

    # Read all rows (skip totally empty)
    all_rows = _read_all_rows(ws)

    if not all_rows:
        wb.close()
        return _empty_result(filepath)

    # Detect header row
    if header_row is None:
        header_row = _detect_header_row(all_rows)

    # Detect data start
    if data_start_row is None:
        data_start_row = _detect_data_start(all_rows, header_row)

    # Extract headers
    headers = _clean_headers(all_rows[header_row - 1])

    # Extract data rows
    rows: list[dict[str, str | None]] = []
    for row_idx in range(data_start_row - 1, len(all_rows)):
        raw = all_rows[row_idx]
        if _is_empty_row(raw):
            continue
        row_dict: dict[str, str | None] = {}
        for col_idx, hdr in enumerate(headers):
            val = raw[col_idx] if col_idx < len(raw) else None
            row_dict[hdr] = _cell_to_str(val)
        rows.append(row_dict)

    wb.close()

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.EXCEL,
            extraction_method=ExtractionMethod.OPENPYXL,
            extraction_confidence=0.95,
        ),
        headers=headers,
        rows=rows,
        raw_header_rows=[
            _to_str_list(all_rows[i])
            for i in range(header_row - 1, min(header_row, len(all_rows)))
        ],
        metadata={
            "sheet_name": ws.title,
            "header_row": header_row,
            "data_start_row": data_start_row,
            "total_sheets": len(wb.sheetnames) if hasattr(wb, "sheetnames") else 1,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _select_sheet(wb: openpyxl.Workbook, sheet_name: str | None):
    if sheet_name:
        return wb[sheet_name]
    # Prefer "Stückliste" if it exists
    for name in wb.sheetnames:
        if "stückliste" in name.lower() or "stueckliste" in name.lower():
            return wb[name]
    return wb.active


def _read_all_rows(ws) -> list[list]:
    """Read all rows from the worksheet, resolving merged cells."""
    rows = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=False):
        vals = []
        for cell in row:
            if isinstance(cell, MergedCell):
                vals.append(None)
            else:
                vals.append(cell.value)
        rows.append(vals)
    return rows


def _detect_header_row(rows: list[list]) -> int:
    """Find the header row by looking for the row with the most non-empty text cells."""
    best_row = 1
    best_score = 0

    for i, row in enumerate(rows[:20]):  # Check first 20 rows
        text_count = sum(1 for v in row if isinstance(v, str) and len(v.strip()) > 1)
        # Prefer rows where many cells are strings (i.e. column headers)
        if text_count > best_score:
            best_score = text_count
            best_row = i + 1  # 1-indexed

    return best_row


def _detect_data_start(rows: list[list], header_row: int) -> int:
    """Find first data row after the header — typically header_row + 1 or + 2."""
    for i in range(header_row, min(header_row + 5, len(rows))):
        row = rows[i]
        # Data rows typically have numbers or non-header text
        has_number = any(isinstance(v, (int, float)) for v in row if v is not None)
        non_empty = sum(1 for v in row if v is not None)
        if has_number and non_empty >= 2:
            return i + 1  # 1-indexed
    return header_row + 1


def _clean_headers(raw_row: list) -> list[str]:
    """Convert a raw row into clean header strings."""
    headers = []
    seen: dict[str, int] = {}
    for val in raw_row:
        h = _cell_to_str(val) or ""
        h = h.strip().replace("\n", " ").replace("\r", " ")
        # Remove excessive whitespace
        h = " ".join(h.split())
        if not h:
            h = f"_col_{len(headers) + 1}"

        # De-duplicate
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0

        headers.append(h)
    return headers


def _cell_to_str(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() if val.strip() else None
    return str(val)


def _to_str_list(row: list) -> list[str | None]:
    return [_cell_to_str(v) for v in row]


def _is_empty_row(row: list) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)


def _empty_result(filepath: Path) -> ParsedBOM:
    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.EXCEL,
            extraction_method=ExtractionMethod.OPENPYXL,
            extraction_confidence=0.0,
        ),
    )
