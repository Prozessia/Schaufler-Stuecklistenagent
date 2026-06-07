"""Batch diagnostic: run the RB-1 deterministic core over every POC PDF.

No LLM. Reports per file whether it is a text-layer PDF (this path) or a scan
(Vision path), and for text-layer PDFs the band/section/row/column outcome so
header-layout variants and robustness gaps surface before wiring.

Usage:
    python scripts/diag_coordinate_batch.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.coordinate_table import (  # noqa: E402
    _assign_cells,
    _cluster_into_bands,
    _extract_words,
    _merge_multiline,
    _segment_into_sections,
)
from src.ingestion.pdf_parser import pdf_has_text_layer  # noqa: E402

INPUT_DIR = Path("data/input/PDF_POC")


def analyse(path: Path) -> str:
    try:
        if not pdf_has_text_layer(path):
            return "SCAN (no text layer) → Vision path, skipped here"
        words = _extract_words(path)
        if not words:
            return "TEXT but 0 words after C1 filter"
        bands = _cluster_into_bands(words)
        secs = _segment_into_sections(bands)
        rows, keys, locs = _assign_cells(secs)
        rows, keys, locs = _merge_multiline(rows, keys, locs, secs)
        anchor = [
            b.band_id
            for s in secs
            for b in s.data_bands
            if not b.is_header and not b.is_continuation
        ]
        pages = len({w.page for w in words})
        empty = sum(1 for r in rows if not any(v for v in r.values()))
        consistent = len(rows) == len(set(keys)) == len(anchor)
        cols = [c.name for c in secs[0].corridors] if secs else []
        junk = sum(
            1
            for r in rows
            if any(v and ("Seite" in str(v) or "Revision" in str(v)) for v in r.values())
        )
        flag = "OK " if consistent and empty == 0 and junk == 0 else "!! "
        return (
            f"{flag}{pages:>2}p | {len(rows):>4} rows | {len(secs):>2} sec | "
            f"empty={empty} junk={junk} consistent={consistent} | "
            f"cols[0]={cols[:8]}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}"


def main() -> None:
    pdfs = sorted(INPUT_DIR.rglob("*.pdf"))
    print(f"{len(pdfs)} PDFs under {INPUT_DIR}\n")
    for path in pdfs:
        rel = path.relative_to(INPUT_DIR)
        print(f"### {rel}")
        print(f"    {analyse(path)}\n")


if __name__ == "__main__":
    main()
