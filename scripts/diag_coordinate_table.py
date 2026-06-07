"""Diagnostic: run the RB-1 deterministic core against a real born-digital PDF.

No LLM, no Azure. Dumps band/section/corridor/row stats so the D1-D5 heuristics
can be judged against reality before wiring the reconstructor into the pipeline.

Usage:
    python scripts/diag_coordinate_table.py "data/input/PDF_POC/ZF/Projekt 7497/Kunde/f156900400_stl.pdf"
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.coordinate_table import (  # noqa: E402
    _assign_cells,
    _cluster_into_bands,
    _extract_words,
    _merge_multiline,
    _raw_column_labels,
    _segment_into_sections,
)
from src.ingestion.pdf_parser import pdf_has_text_layer  # noqa: E402


def main(pdf_path: str) -> None:
    path = Path(pdf_path)
    print(f"FILE: {path.name}")
    print(f"text_layer: {pdf_has_text_layer(path)}")

    words = _extract_words(path)
    pages = sorted({w.page for w in words})
    print(f"words: {len(words)} | pages with text: {len(pages)} ({pages[0]}..{pages[-1]})")

    bands = _cluster_into_bands(words)
    per_page = Counter(b.page for b in bands)
    print(f"bands total: {len(bands)} | per-page bands: "
          f"min={min(per_page.values())} max={max(per_page.values())} "
          f"median={sorted(per_page.values())[len(per_page)//2]}")

    sections = _segment_into_sections(bands)
    header_bands = sum(1 for s in sections for b in s.data_bands if b.is_header)
    print(f"sections: {len(sections)} | header bands inside sections: {header_bands}")
    for i, sec in enumerate(sections[:6]):
        cols = [c.name for c in sec.corridors]
        print(f"  section {i}: {len(sec.corridors)} corridors, "
              f"{len(sec.data_bands)} data bands | cols={cols}")
    if len(sections) > 6:
        print(f"  ... +{len(sections) - 6} more sections")

    rows, keys, locations = _assign_cells(sections)
    rows, keys, locations = _merge_multiline(rows, keys, locations, sections)
    pdf_row_bands = [
        b.band_id for s in sections for b in s.data_bands
        if not b.is_header and not b.is_continuation
    ]
    print(f"\nROWS emitted: {len(rows)} | distinct row_keys: {len(set(keys))} "
          f"| pdf_row_bands (anchor): {len(pdf_row_bands)}")

    labels = _raw_column_labels(sections)
    print(f"raw column labels (union): {labels}")

    print("\n--- first 8 rows (deterministic, pre-LLM) ---")
    for r, k in list(zip(rows, keys))[:8]:
        compact = {kk: vv for kk, vv in r.items() if vv}
        print(f"  [{k}] {compact}")

    # Empty-cell sanity: how many rows have NO non-empty cell (corridor misses)?
    empty_rows = sum(1 for r in rows if not any(v for v in r.values()))
    print(f"\nrows with all cells empty (corridor miss): {empty_rows}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
