"""Test header detection on specific failing files."""

import sys, io
from pathlib import Path

# Fix encoding for Czech/special chars
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz

fitz.TOOLS.mupdf_warnings(False)

from src.ingestion.pdf_parser import _find_header_row, _score_header_row, _norm
from src.ingestion.file_router import infer_customer

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "input" / "PDF_POC"
BAD_CUSTOMERS = {
    "Ford",
    "GF",
    "Linamar FR",
    "Ljunghaell_Tschechien",
    "Scania",
    "TCG",
    "ZF",
}

seen = set()
for f in sorted(DATA_DIR.rglob("*.pdf")):
    if "CadCam" in f.name:
        continue
    customer = infer_customer(f)
    if customer not in BAD_CUSTOMERS or customer in seen:
        continue
    seen.add(customer)

    doc = fitz.open(f)
    page = doc[0]
    finder = page.find_tables()
    tables = finder.tables if hasattr(finder, "tables") else []

    # Find widest table
    best_table = None
    best_cols = 0
    for table in tables:
        try:
            extracted = table.extract()
        except:
            continue
        if not extracted:
            continue
        cols = max(len(r) for r in extracted)
        if cols > best_cols:
            best_cols = cols
            best_table = extracted

    if not best_table:
        print(f"\n{customer}: NO TABLE FOUND")
        continue

    print(f"\n{'='*60}")
    print(f"{customer}: {best_cols} cols, {len(best_table)} rows in first-page table")
    print(f"{'='*60}")

    # Score first 8 rows
    scan = min(8, len(best_table))
    for i in range(scan):
        row = list(best_table[i]) + [None] * (best_cols - len(best_table[i]))
        row = row[:best_cols]
        score = _score_header_row(row, best_cols)
        # Show first 3 non-empty cells
        cells = [_norm(c) for c in row if _norm(c)][:4]
        cells_str = " | ".join(c[:30] for c in cells)
        print(f"  Row {i}: score={score:.2f}  [{cells_str}]")

    # What does _find_header_row pick?
    padded_rows = []
    for r in best_table[:scan]:
        padded = list(r) + [None] * (best_cols - len(r))
        padded_rows.append(padded[:best_cols])

    best_idx = _find_header_row(padded_rows, best_cols)
    print(f"  -> PICKED header row: {best_idx}")

    doc.close()
