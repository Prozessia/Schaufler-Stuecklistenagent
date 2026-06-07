"""Material-matching diagnostic / regression gate (no Azure, no LLM).

Runs the deterministic reconstructor over every born-digital POC PDF, finds the
material-ish column, and reports per file: how many material values match the
catalog vs stay no_match, broken down by method. Critically it lists the values
matched via the new ``werkstoff_nr_format`` method AND the values that look like a
DIN Werkstoffnummer but still miss — so a regression or a false-positive is
visible at a glance.

Usage:
    python scripts/diag_material_match.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.coordinate_table import reconstruct_table  # noqa: E402
from src.ingestion.pdf_common import ExtractionError  # noqa: E402
from src.transform.master_data_matcher import get_material_catalog  # noqa: E402

INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "input" / "PDF_POC"
_MATERIAL_HEADER = ("werkst", "vergüt", "material", "matér", "materiale", "norma")
# A token that structurally IS a DIN Werkstoffnummer (the class-A/B signal).
_WERKSTOFF_NR = re.compile(r"\b\d[.\-]\d{4}\b")


class _StubLLM:  # reconstruct_table never calls the LLM on the deterministic path
    pass


def _material_column(headers: list[str]) -> str | None:
    for h in headers:
        if any(k in h.lower() for k in _MATERIAL_HEADER):
            return h
    return None


async def _analyse(path: Path) -> None:
    try:
        bom = await reconstruct_table(path, _StubLLM())
    except ExtractionError as exc:
        print(f"  DECLINED (Vision): {exc}".split(" — ")[0])
        return

    col = _material_column(bom.headers)
    if not col:
        print("  no material column detected — skipped")
        return

    cat = get_material_catalog()
    methods: Counter[str] = Counter()
    format_matched: list[str] = []
    still_missing_nr: list[str] = []

    for row in bom.rows:
        value = str(row.get(col) or "").strip()
        if not value:
            continue
        result = cat.match(value)
        methods[result.method] += 1
        if result.method == "werkstoff_nr_format":
            format_matched.append(value)
        elif result.method in ("no_match", "empty") and _WERKSTOFF_NR.search(value):
            still_missing_nr.append(value)

    total = sum(methods.values())
    no_match = methods.get("no_match", 0) + methods.get("empty", 0)
    print(f"  col={col!r} | {total} values | matched={total - no_match} no_match={no_match}")
    print(f"  methods={dict(methods)}")
    if format_matched:
        sample = Counter(format_matched).most_common(8)
        print(f"  NEW format-matched (inspect = must all be real Werkstoff-Nr): {sample}")
    if still_missing_nr:
        sample = Counter(still_missing_nr).most_common(8)
        print(f"  still-missing DIN-Nr (format not yet recognised): {sample}")


async def main() -> None:
    pdfs = sorted(p for p in INPUT_DIR.rglob("*.pdf"))
    print(f"Material-match diagnostic over {len(pdfs)} POC PDFs\n")
    for pdf in pdfs:
        print(f"### {pdf.relative_to(INPUT_DIR)}")
        try:
            await _analyse(pdf)
        except Exception as exc:  # noqa: BLE001 — diagnostic, never fatal
            print(f"  ERROR: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
