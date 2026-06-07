"""Quick diagnostic: check header quality for all BOM files."""

import os
import sys
from pathlib import Path

# Suppress MuPDF warnings via API
try:
    import fitz

    fitz.TOOLS.mupdf_warnings(False)
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.structure_normalizer import parse_file
from src.ingestion.file_router import infer_customer

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "input" / "PDF_POC"

seen = set()
results = []

for f in sorted(DATA_DIR.rglob("*")):
    if not f.is_file() or f.suffix.lower() not in (".pdf", ".xlsx"):
        continue
    if "CadCam_Stuecklistenvorlage" in f.name:
        continue

    customer = infer_customer(f)
    if customer in seen:
        continue
    seen.add(customer)

    try:
        bom = parse_file(f)
        bad = sum(1 for h in bom.headers if h.startswith("_col_"))
        status = "BAD" if bad > len(bom.headers) * 0.5 else "OK"
        hdrs = str(bom.headers[:5])[:80]
        results.append(
            f"[{status:3s}] {customer:25s} | {bom.total_columns:2d} cols {bom.total_rows:4d} rows | {hdrs}"
        )
    except Exception as e:
        results.append(f"[ERR] {customer:25s} | {f.name} | {e}")

# Write ONLY to file — avoid terminal where MuPDF noise pollutes output
out_path = (
    Path(__file__).resolve().parents[1] / "data" / "test_outputs" / "header_check.txt"
)
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text("\n".join(results), encoding="utf-8")

# Print minimal confirmation only
sys.stderr.write(f"Done: {len(results)} results -> {out_path}\n")
