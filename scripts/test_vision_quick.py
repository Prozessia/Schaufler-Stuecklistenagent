"""Quick Vision test: GF only, first 2 pages only."""
import asyncio
import os
import sys
import json
import time

os.environ["PYMUPDF_MESSAGE"] = "path:" + os.devnull
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.llm.azure_openai import AzureOpenAILLM
from src.ingestion.pdf_parser import (
    _render_pdf_pages,
    _detect_columns_via_vision,
    _extract_single_page,
    _post_validate_extraction,
    _MIN_COLS,
)

GF_PATH = "data/input/PDF_POC/GF/STL_08.05.13.pdf"


async def main():
    llm = AzureOpenAILLM()

    print(f"Rendering GF pages at 200 DPI...")
    t0 = time.time()
    images = _render_pdf_pages(GF_PATH, dpi=200)
    print(f"  Rendered {len(images)} pages in {time.time()-t0:.1f}s")

    # Only test first 2 pages
    test_pages = images[:2]

    # Phase A — column detection (page 1)
    print(f"\nPhase A: detecting columns from page 1...")
    t0 = time.time()
    columns = await _detect_columns_via_vision(test_pages[0], llm)
    print(f"  Done in {time.time()-t0:.1f}s")

    if not columns or len(columns) < _MIN_COLS:
        print(f"  Retry with extended prompt...")
        t0 = time.time()
        columns = await _detect_columns_via_vision(test_pages[0], llm, retry=True)
        print(f"  Retry done in {time.time()-t0:.1f}s")

    if not columns:
        print("  FAILED: no columns detected")
        return

    print(f"  Columns ({len(columns)}): {columns}")

    # Phase B — extract from pages 1 and 2
    all_rows = []
    for i, img in enumerate(test_pages):
        print(f"\nPhase B: extracting page {i+1}...")
        t0 = time.time()
        rows = await _extract_single_page(img, columns, llm, page_num=i+1)
        elapsed = time.time() - t0
        print(f"  Done in {elapsed:.1f}s — {len(rows)} rows")
        all_rows.extend(rows)

    # Post-validate
    all_rows, flags = _post_validate_extraction(all_rows, columns)

    print(f"\n{'='*50}")
    print(f"RESULT: {len(all_rows)} rows from 2 pages, {len(flags)} flagged rows")
    print(f"Columns: {columns}")

    for i, row in enumerate(all_rows[:5]):
        clean = {k: v for k, v in row.items() if v is not None}
        print(f"  Row {i}: {json.dumps(clean, ensure_ascii=False)}")

    if len(all_rows) > 0:
        print(f"\n*** GF WORKS! Previously was EMPTY, now has {len(all_rows)} rows ***")
    else:
        print(f"\n*** GF STILL EMPTY — need to investigate ***")


if __name__ == "__main__":
    asyncio.run(main())
