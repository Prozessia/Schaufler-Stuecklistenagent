"""
Extracts all BOM rows from the reference PDF and builds a completely new
demo PDF — no logos, no customer/company references, neutral header.
"""

import fitz
from collections import defaultdict
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame

SRC = Path("data/input/PDF_POC/ZF/Projekt 7497/Kunde/f156900400_stl.pdf")
OUT = Path("data/test_outputs/demo_stueckliste_neu.pdf")

# Column x-boundaries derived from original PDF layout analysis
COLS = {
    "pos": (0, 46),
    "anzahl": (46, 67),
    "modellname": (67, 187),
    "bezeichnung": (187, 320),
    "fertigmasse": (320, 398),
    "verguetung": (398, 441),
    "material": (441, 531),
    "haerte": (531, 580),
    "rm": (580, 622),
    "masse": (622, 661),
    "hinweise": (661, 840),
}

Y_DATA_START = 138  # first data row starts below this y
Y_DATA_END = 560  # footer area starts above this y
DATA_SANITIZE = [
    ("SV190ZF", "SV190KD"),
    ("ZF_N", "KD_N"),
    ("ZFN", "KDN"),
    ("JLR", "HST"),
    ("Schaufler", "Formenbau AG"),
]


def sanitize_row(row: dict) -> dict:
    out = {}
    for col, val in row.items():
        for old, new in DATA_SANITIZE:
            val = val.replace(old, new)
        out[col] = val
    return out


Y_TOLERANCE = 4  # group words within ±4pt into same row


def col_for_x(x: float) -> str | None:
    for name, (lo, hi) in COLS.items():
        if lo <= x < hi:
            return name
    return None


def extract_rows(doc: fitz.Document) -> list[dict]:
    """Extract structured BOM rows from all pages."""
    rows: list[dict] = []

    for pg_idx in range(len(doc)):
        page = doc[pg_idx]
        words = page.get_text("words")

        # Collect data words (skip header and footer area)
        by_y: dict[int, list[tuple[float, str, str]]] = defaultdict(list)
        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            if y0 < Y_DATA_START or y0 > Y_DATA_END:
                continue
            col = col_for_x(x0)
            if col:
                y_key = round(y0 / Y_TOLERANCE) * Y_TOLERANCE
                by_y[y_key].append((x0, col, text))

        for y_key in sorted(by_y.keys()):
            cell_parts: dict[str, list[str]] = defaultdict(list)
            for x, col, text in sorted(by_y[y_key]):
                cell_parts[col].append(text)

            row = {col: " ".join(parts) for col, parts in cell_parts.items()}

            # Only keep lines that look like real data (have a position number)
            if row.get("pos", "").strip() and "-" in row.get("pos", ""):
                rows.append(row)

    return rows


# ─── PDF generation ───────────────────────────────────────────────────────────

PAGE_W, PAGE_H = landscape(A4)
MARGIN = 10 * mm

DEMO_META = {
    "bezeichnung": "Getriebegehaeuse B3-HST",
    "projekt_nr": "4.2350.1",
    "datum_erstellt": "20.11.2015",
    "revision": "1",
    "firma": "Formenbau AG",
    "doc_nr": "FB-WZ-BM-001",
}

COL_WIDTHS = [
    22 * mm,  # Pos.Nr.
    14 * mm,  # Anz.
    52 * mm,  # Modellname
    50 * mm,  # Bezeichnung
    32 * mm,  # Fertigmaße
    20 * mm,  # Vergütung
    22 * mm,  # Material
    18 * mm,  # Härte HRC
    14 * mm,  # Rm
    18 * mm,  # Masse kg
    42 * mm,  # Hinweise
]

COL_HEADERS = [
    "Pos.\nNr.",
    "Anz.",
    "Modellname",
    "Bezeichnung",
    "Fertigmaße",
    "Vergütung",
    "Material",
    "Härte\nHRC",
    "Rm\nN/mm²",
    "Masse\nkg",
    "Hinweise",
]

ROWS_PER_PAGE = 28  # data rows per page before page break


def build_header(c: canvas.Canvas, page_num: int, total_pages: int) -> None:
    """Draw the BOM header box on a page."""
    from reportlab.lib.colors import black, white, HexColor

    header_h = 28 * mm
    y_top = PAGE_H - MARGIN
    box_x = MARGIN
    box_w = PAGE_W - 2 * MARGIN

    # Title box
    c.setFillColor(HexColor("#1a3c6e"))
    c.rect(box_x, y_top - 8 * mm, box_w, 8 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(PAGE_W / 2, y_top - 5.5 * mm, "Stückliste")

    # Info row
    c.setFillColor(black)
    c.setFont("Helvetica", 7.5)
    info_y = y_top - 13 * mm
    c.drawString(box_x, info_y, f"Dok.-Nr.: {DEMO_META['doc_nr']}")
    c.drawString(
        box_x + 55 * mm, info_y, f"Projektauftrag Nr.: {DEMO_META['projekt_nr']}"
    )
    c.drawString(box_x + 140 * mm, info_y, "Formfertigstellungstermin:")

    info_y2 = y_top - 19 * mm
    c.drawString(box_x, info_y2, f"Revision {DEMO_META['revision']}")
    c.drawString(box_x + 55 * mm, info_y2, f"Bezeichnung: {DEMO_META['bezeichnung']}")
    c.drawString(box_x + 155 * mm, info_y2, f"Formenaufbaufirma: {DEMO_META['firma']}")

    info_y3 = y_top - 25 * mm
    c.drawString(box_x, info_y3, f"Seite {page_num} von {total_pages}")
    c.drawString(box_x + 55 * mm, info_y3, f"Form Nr.: {DEMO_META['projekt_nr']}")
    c.drawString(box_x + 155 * mm, info_y3, f"Erstellt: {DEMO_META['datum_erstellt']}")

    # Legend
    legend_y = y_top - 32 * mm
    c.setFont("Helvetica", 6)
    c.drawString(
        box_x,
        legend_y,
        "Legende: vgn=vergueten; vgt=verguetet; g=gasnitr.; p=plasmanitrieren; u=uni twin beschichten; 3D=3D-geschmiedet",
    )

    # Horizontal line under header
    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    c.line(box_x, y_top - 35 * mm, box_x + box_w, y_top - 35 * mm)


def row_to_cells(row: dict) -> list[str]:
    return [
        row.get("pos", ""),
        row.get("anzahl", ""),
        row.get("modellname", ""),
        row.get("bezeichnung", ""),
        row.get("fertigmasse", ""),
        row.get("verguetung", ""),
        row.get("material", ""),
        row.get("haerte", ""),
        row.get("rm", ""),
        row.get("masse", ""),
        row.get("hinweise", ""),
    ]


TARGET_PAGES = 35


def build_pdf(rows: list[dict]) -> None:
    from reportlab.pdfgen.canvas import Canvas

    c = Canvas(str(OUT), pagesize=landscape(A4))

    header_height = 38 * mm
    footer_height = 8 * mm
    table_top = PAGE_H - MARGIN - header_height
    table_bottom = MARGIN + footer_height
    available_h = table_top - table_bottom

    row_h = 5.8 * mm
    col_header_h = 8 * mm
    rows_per_page = int((available_h - col_header_h) / row_h)

    # Pad rows so we have exactly TARGET_PAGES pages
    needed = TARGET_PAGES * rows_per_page
    while len(rows) < needed:
        rows = rows + rows  # double until enough
    rows = rows[:needed]

    chunks = [rows[i : i + rows_per_page] for i in range(0, len(rows), rows_per_page)]
    total_pages = len(chunks)

    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.colors import black, white, HexColor, lightgrey

    LIGHT_BLUE = HexColor("#dce6f1")
    HEADER_BG = HexColor("#1a3c6e")

    for pg_idx, chunk in enumerate(chunks):
        c.setPageSize(landscape(A4))

        build_header(c, pg_idx + 1, total_pages)

        # Build table data
        table_data = [COL_HEADERS] + [row_to_cells(r) for r in chunk]

        tbl = Table(table_data, colWidths=COL_WIDTHS, rowHeights=None)

        style = TableStyle(
            [
                # Column header row
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 6.5),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, LIGHT_BLUE]),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 6.5),
                ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.3, HexColor("#aaaaaa")),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
        tbl.setStyle(style)

        w, h = tbl.wrapOn(c, PAGE_W - 2 * MARGIN, available_h)
        tbl.drawOn(c, MARGIN, table_top - h)

        # Footer
        c.setFont("Helvetica", 6)
        c.setFillColor(black)
        c.drawString(
            MARGIN,
            MARGIN + 2 * mm,
            f"Dateiname: K987600100_STL   |   gedruckt am: 2024-01-15   |   FB-WZ-BM-001",
        )

        if pg_idx < len(chunks) - 1:
            c.showPage()

    c.save()
    print(f"Saved {total_pages} pages -> {OUT}")


def main() -> None:
    doc = fitz.open(str(SRC))
    print("Extracting rows...")
    rows = extract_rows(doc)
    rows = [sanitize_row(r) for r in rows]
    print(f"  {len(rows)} data rows extracted")
    build_pdf(rows)


if __name__ == "__main__":
    main()
