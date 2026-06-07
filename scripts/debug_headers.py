"""Debug: show raw table extraction for each failing customer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz

fitz.TOOLS.mupdf_warnings(False)

from src.ingestion.file_router import infer_customer

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "input" / "PDF_POC"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "test_outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Only examine the BAD customers
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
lines = []

for f in sorted(DATA_DIR.rglob("*")):
    if not f.is_file() or f.suffix.lower() != ".pdf":
        continue
    if "CadCam_Stuecklistenvorlage" in f.name:
        continue

    customer = infer_customer(f)
    if customer not in BAD_CUSTOMERS or customer in seen:
        continue
    seen.add(customer)

    lines.append(f"\n{'='*80}")
    lines.append(f"CUSTOMER: {customer}")
    lines.append(f"FILE: {f.name}")
    lines.append(f"{'='*80}")

    doc = fitz.open(f)

    # Show first page table extraction
    for page_num, page in enumerate(doc):
        if page_num > 1:  # Only first 2 pages
            break

        finder = page.find_tables()
        tables = finder.tables if hasattr(finder, "tables") else []

        lines.append(f"\n  Page {page_num + 1}: {len(tables)} tables found")

        for t_idx, table in enumerate(tables):
            try:
                extracted = table.extract()
            except Exception as e:
                lines.append(f"    Table {t_idx}: extraction error: {e}")
                continue

            if not extracted:
                continue

            col_count = max(len(r) for r in extracted)
            lines.append(f"    Table {t_idx}: {len(extracted)} rows x {col_count} cols")

            # Show first 5 rows
            for r_idx, row in enumerate(extracted[:5]):
                line = " | ".join(str(v)[:30] if v else "" for v in row)
                lines.append(f"      Row {r_idx}: {line[:200]}")

    # Also show raw text from first page for comparison
    first_page = doc[0]
    text = first_page.get_text()
    text_lines = text.split("\n")[:30]
    lines.append(f"\n  Raw text (first 30 lines):")
    for tl in text_lines:
        if tl.strip():
            lines.append(f"    {tl.strip()[:120]}")

    doc.close()

output = "\n".join(lines)
out_path = OUT_DIR / "debug_bad_headers.txt"
out_path.write_text(output, encoding="utf-8")
print(f"Done: {len(seen)} customers -> {out_path}")
