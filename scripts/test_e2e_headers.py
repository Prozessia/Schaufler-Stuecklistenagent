"""Quick test: does the improved header detection work end-to-end?"""

import sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.structure_normalizer import parse_file
from src.ingestion.file_router import infer_customer

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "input" / "PDF_POC"
OUT = Path(__file__).resolve().parents[1] / "data" / "test_outputs" / "header_e2e.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

lines = []
seen = set()
for f in sorted(DATA_DIR.rglob("*")):
    if not f.is_file() or f.suffix.lower() != ".pdf":
        continue
    if "CadCam" in f.name:
        continue
    customer = infer_customer(f)
    if customer in seen:
        continue
    seen.add(customer)
    try:
        bom = parse_file(f)
        bad = sum(1 for h in bom.headers if h.startswith("_col_"))
        status = "BAD" if bad > len(bom.headers) * 0.5 else "OK"
        meta = bom.metadata or {}
        hdr_row = meta.get("detected_header_row", "?")
        hdrs = str(bom.headers[:5])[:80]
        lines.append(
            f"[{status:3s}] {customer:25s} hdr_row={hdr_row} | {bom.total_columns:2d} cols {bom.total_rows:4d} rows | {hdrs}"
        )
    except Exception as e:
        import traceback

        lines.append(f"[ERR] {customer:25s} | {e}")
        lines.append(traceback.format_exc())

OUT.write_text("\n".join(lines), encoding="utf-8")
for l in lines:
    print(l)
