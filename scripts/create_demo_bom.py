"""
Creates a sanitized 35-page demo BOM from the internal reference PDF.
Removes: company names (Schaufler, ZF, JLR), project-specific identifiers.
Replaces with: neutral placeholders.
Extends: 19 pages → 35 pages by duplicating data pages.
"""

import fitz
import shutil
from pathlib import Path

SRC = Path("data/input/PDF_POC/ZF/Projekt 7497/Kunde/f156900400_stl.pdf")
OUT = Path("data/test_outputs/demo_stueckliste_35seiten.pdf")

# Text replacements (order matters — longest first to avoid partial matches)
REPLACEMENTS = [
    ("FB-NU-WB-009", "FB-WZ-BM-001"),
    ("F156900400_STL", "K987600100_STL"),
    ("F156900400", "K987600100"),
    ("GG 8HP45 JLR HIS VT", "Getriebegehaeuse B3-HST VT"),
    ("SV190ZF_N_1-4_AS", "SV190KD_N_1-4_AS"),
    ("SV190ZF_N_1-4_ES", "SV190KD_N_1-4_ES"),
    ("ZF_N_1-4", "KD_N_1-4"),
    ("ZFN", "KDN"),
    ("JLR", "HST"),
    ("Schaufler", "Formenbau AG"),
    ("1.5690.4", "4.2350.1"),
]

HEADER_Y_MAX = 75  # y-coordinate threshold for the page header area
# ZF vector logo location (bottom-right corner, verified by get_drawings() analysis)
LOGO_RECT = fitz.Rect(775, 550, 815, 577)


def redact_text(
    page: fitz.Page, old: str, new: str, font: str = "helv", size: float = 10.5
) -> int:
    """Redact all occurrences of old and insert new text. Returns count of replacements."""
    rects = page.search_for(old)
    for rect in rects:
        page.add_redact_annot(
            rect,
            new,
            fontname=font,
            fontsize=size,
            fill=(1, 1, 1),
            text_color=(0, 0, 0),
            align=fitz.TEXT_ALIGN_LEFT,
        )
    if rects:
        page.apply_redactions()
    return len(rects)


def redact_page_total(page: fitz.Page, old_total: str, new_total: str) -> None:
    """Replace the page total number (e.g. '19' → '35') only in the header area."""
    words = page.get_text("words")
    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if text == old_total and y0 < HEADER_Y_MAX:
            rect = fitz.Rect(x0, y0, x1, y1)
            page.add_redact_annot(
                rect,
                new_total,
                fontname="helv",
                fontsize=10.5,
                fill=(1, 1, 1),
                text_color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_LEFT,
            )
            page.apply_redactions()


def redact_page_number(page: fitz.Page, old_num: str, new_num: str) -> None:
    """Replace the current page number only in the header area."""
    words = page.get_text("words")
    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        # Page number appears between 'Seite' (x≈30) and 'von' (x≈73), y≈62
        if text == old_num and y0 < HEADER_Y_MAX and 55 < x0 < 75:
            rect = fitz.Rect(x0, y0, x1, y1)
            page.add_redact_annot(
                rect,
                new_num,
                fontname="helv",
                fontsize=10.5,
                fill=(1, 1, 1),
                text_color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_LEFT,
            )
            page.apply_redactions()
            return


def sanitize_page(
    page: fitz.Page, new_page_num: int | None = None, old_page_num: int | None = None
) -> None:
    """Apply all redactions to a single page."""
    for old, new in REPLACEMENTS:
        redact_text(page, old, new)
    redact_page_total(page, "19", "35")
    if new_page_num is not None and old_page_num is not None:
        redact_page_number(page, str(old_page_num), str(new_page_num))
    # Cover ZF vector logo with a white filled rectangle
    page.draw_rect(LOGO_RECT, fill=(1, 1, 1), color=(1, 1, 1), width=0)


def main() -> None:
    src = fitz.open(str(SRC))
    original_count = len(src)  # 19
    target_count = 35
    extra_needed = target_count - original_count  # 16

    out_doc = fitz.open()

    # Copy and sanitize original pages
    for i in range(original_count):
        out_doc.insert_pdf(src, from_page=i, to_page=i)
        page = out_doc[-1]
        sanitize_page(page)  # keep original page numbers (1-19)

    # Duplicate pages 3-18 (0-indexed: 2-17) → adds 16 pages → total 35
    for extra_idx in range(extra_needed):
        src_page_idx = 2 + extra_idx  # cycle through pages 3-18
        new_page_num = original_count + extra_idx + 1  # 20-35
        old_page_num = src_page_idx + 1  # 3-18 (what's printed on the original page)

        out_doc.insert_pdf(src, from_page=src_page_idx, to_page=src_page_idx)
        page = out_doc[-1]
        sanitize_page(page, new_page_num=new_page_num, old_page_num=old_page_num)

    out_doc.save(str(OUT), garbage=4, deflate=True)
    print(f"Saved {len(out_doc)} pages -> {OUT}")
    src.close()
    out_doc.close()


if __name__ == "__main__":
    main()
