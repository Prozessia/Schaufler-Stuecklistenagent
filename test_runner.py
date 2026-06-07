"""POC-Auswertung: Verarbeitet alle Kunden-PDFs und erstellt ein
Entscheidungs-PDF mit Gruen/Gelb/Rot-Verteilung pro Kunde und gesamt.

Erstellt fuer: Juergen Maler (Schaufler Tooling)
Zweck: Entscheidungsgrundlage ob Projekt gestartet wird.

Usage:
    python test_runner.py                   # Alle PDFs verarbeiten
    python test_runner.py --timeout 30      # Timeout pro Datei (Minuten)
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_ROOT / "data" / "input"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RUN_AT = datetime.now()
RUN_STAMP = RUN_AT.strftime("%Y%m%d_%H%M%S")
OUTPUT_PDF = OUTPUT_DIR / f"poc_auswertung_{RUN_STAMP}.pdf"
TEMP_DB_PATH = OUTPUT_DIR / f".test_runner_jobs_{RUN_STAMP}.db"
TIMEOUT_MINUTES = 25
TIMEOUT_SECONDS = TIMEOUT_MINUTES * 60
PARALLEL_WORKERS = 1  # 1 = sequenziell, kein 429-Rate-Limit

os.environ["BOM_MAPPER_JOB_DB_PATH"] = str(TEMP_DB_PATH)
os.environ["PYMUPDF_MESSAGE"] = "path:" + os.devnull
load_dotenv(PROJECT_ROOT / ".env")

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Flowable,
        LongTable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(
        "Fehlende Abhaengigkeit 'reportlab'. Bitte 'pip install -r requirements.txt' ausfuehren."
    ) from exc

from src.api.job_store import job_store
from src.api.pipeline_runner import run_pipeline
from src.scoring.audit_trail import BomAuditTrail

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FileRunResult:
    display_name: str
    customer: str
    processed: bool
    include_in_kpi: bool
    green_count: int
    yellow_count: int
    red_count: int
    neutral_count: int
    total_scored: int
    green_pct: float | None
    yellow_pct: float | None
    red_pct: float | None
    total_fields: int | None
    note: str
    # RB-1: zero-data-loss / extraction provenance per file.
    completeness_guaranteed: bool = False
    guard_basis: str = "none"


@dataclass
class CustomerSummary:
    name: str
    files_total: int = 0
    files_processed: int = 0
    files_failed: int = 0
    files_excluded_quality: int = 0
    green_total: int = 0
    yellow_total: int = 0
    red_total: int = 0
    neutral_total: int = 0
    scored_total: int = 0
    guaranteed_files: int = 0  # RB-1: files with deterministically guaranteed completeness
    file_results: list[FileRunResult] = field(default_factory=list)

    @property
    def green_pct(self) -> float:
        return self.green_total / self.scored_total * 100 if self.scored_total else 0.0

    @property
    def yellow_pct(self) -> float:
        return self.yellow_total / self.scored_total * 100 if self.scored_total else 0.0

    @property
    def red_pct(self) -> float:
        return self.red_total / self.scored_total * 100 if self.scored_total else 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COLOR_GREEN = colors.HexColor("#28a745")
COLOR_YELLOW = colors.HexColor("#ffc107")
COLOR_RED = colors.HexColor("#dc3545")
COLOR_GREEN_BG = colors.HexColor("#d4edda")
COLOR_YELLOW_BG = colors.HexColor("#fff3cd")
COLOR_RED_BG = colors.HexColor("#f8d7da")
COLOR_HEADER_BG = colors.HexColor("#243447")
COLOR_LIGHT_BG = colors.HexColor("#f7f9fb")
COLOR_BORDER = colors.HexColor("#c9d2da")
COLOR_TEXT_DARK = colors.HexColor("#162534")
COLOR_TEXT_MID = colors.HexColor("#334e68")


def find_pdf_files(input_dir: Path) -> list[Path]:
    files = [
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    ]
    return sorted(files)


def display_name_for(path: Path) -> str:
    try:
        return str(path.relative_to(INPUT_DIR)).replace("\\", "/")
    except ValueError:
        return path.name


def get_customer_name(path: Path) -> str:
    try:
        rel = path.relative_to(INPUT_DIR / "PDF_POC")
        return rel.parts[0] if rel.parts else path.stem
    except ValueError:
        try:
            rel = path.relative_to(INPUT_DIR)
            return rel.parts[0] if rel.parts else path.stem
        except ValueError:
            return path.stem


def normalize_note(note: str, *, limit: int = 140) -> str:
    clean = re.sub(r"\s+", " ", note or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def metric_or_dash(value: float | int | None, *, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return f"{value:.1f}{suffix}"


def _is_kpi_eligible(audit: BomAuditTrail) -> bool:
    """Exclude files that are effectively unprocessable from KPI percentages.

    This keeps KPI fair: parse disasters are reported as notes, not as RED inflation.
    """
    if audit.total_scored == 0:
        return False

    if audit.green_count == 0 and audit.yellow_count == 0 and audit.red_count >= 10:
        return False

    if audit.total_scored >= 30 and audit.red_pct >= 97.0:
        return False

    return True


# ---------------------------------------------------------------------------
# Ampel-Balken (inline traffic-light bar for the PDF)
# ---------------------------------------------------------------------------


class AmpelBalken(Flowable):
    """Draws a horizontal stacked bar in green/yellow/red."""

    def __init__(
        self,
        green_pct: float,
        yellow_pct: float,
        red_pct: float,
        width: float = 180,
        height: float = 14,
    ):
        super().__init__()
        self.green_pct = green_pct
        self.yellow_pct = yellow_pct
        self.red_pct = red_pct
        self.bar_width = width
        self.bar_height = height
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        total = self.green_pct + self.yellow_pct + self.red_pct
        if total <= 0:
            return

        x = 0
        for pct, color in [
            (self.green_pct, COLOR_GREEN),
            (self.yellow_pct, COLOR_YELLOW),
            (self.red_pct, COLOR_RED),
        ]:
            w = (pct / total) * self.bar_width if total > 0 else 0
            if w > 0.5:
                c.setFillColor(color)
                c.roundRect(x, 0, w, self.bar_height, 2, fill=1, stroke=0)
                if w > 18:
                    c.setFillColor(
                        colors.white if color != COLOR_YELLOW else COLOR_TEXT_DARK
                    )
                    c.setFont("Helvetica-Bold", 7)
                    c.drawCentredString(x + w / 2, 4, f"{pct:.0f}%")
                x += w


# ---------------------------------------------------------------------------
# Customer aggregation
# ---------------------------------------------------------------------------


def aggregate_by_customer(results: list[FileRunResult]) -> list[CustomerSummary]:
    by_name: dict[str, CustomerSummary] = {}
    for r in results:
        cs = by_name.setdefault(r.customer, CustomerSummary(name=r.customer))
        cs.files_total += 1
        cs.file_results.append(r)
        if r.processed:
            cs.files_processed += 1
            if r.completeness_guaranteed:
                cs.guaranteed_files += 1
            if r.include_in_kpi:
                cs.green_total += r.green_count
                cs.yellow_total += r.yellow_count
                cs.red_total += r.red_count
                cs.neutral_total += r.neutral_count
                cs.scored_total += r.total_scored
            else:
                cs.files_excluded_quality += 1
        else:
            cs.files_failed += 1

    return sorted(by_name.values(), key=lambda c: (-c.green_pct, c.name.lower()))


# ---------------------------------------------------------------------------
# PDF report builder
# ---------------------------------------------------------------------------


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleCustom",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=COLOR_TEXT_DARK,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleCustom",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=14,
            textColor=COLOR_TEXT_MID,
            spaceAfter=10,
        ),
        "body": ParagraphStyle(
            "BodyCustom",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=COLOR_TEXT_MID,
        ),
        "body_bold": ParagraphStyle(
            "BodyBoldCustom",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=14,
            textColor=COLOR_TEXT_DARK,
        ),
        "section": ParagraphStyle(
            "SectionCustom",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=COLOR_TEXT_DARK,
            spaceBefore=6,
            spaceAfter=6,
        ),
        "table_header": ParagraphStyle(
            "TableHeaderCustom",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.white,
        ),
        "table_cell": ParagraphStyle(
            "TableCellCustom",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=COLOR_TEXT_DARK,
        ),
        "table_cell_bold": ParagraphStyle(
            "TableCellBoldCustom",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=COLOR_TEXT_DARK,
        ),
        "summary_label": ParagraphStyle(
            "SummaryLabel",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=COLOR_TEXT_DARK,
        ),
        "summary_value": ParagraphStyle(
            "SummaryValue",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=COLOR_TEXT_DARK,
        ),
        "big_number": ParagraphStyle(
            "BigNumber",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=30,
            textColor=COLOR_TEXT_DARK,
            alignment=1,
        ),
        "big_label": ParagraphStyle(
            "BigLabel",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=11,
            textColor=COLOR_TEXT_MID,
            alignment=1,
        ),
    }


def _build_kpi_boxes(customers: list[CustomerSummary], styles) -> Table:
    """Build the 4 big KPI boxes at the top of the summary."""
    total_green = sum(c.green_total for c in customers)
    total_yellow = sum(c.yellow_total for c in customers)
    total_red = sum(c.red_total for c in customers)
    total_scored = sum(c.scored_total for c in customers)
    avg_green = total_green / total_scored * 100 if total_scored else 0
    processed = sum(c.files_processed for c in customers)
    included = sum(c.files_processed - c.files_excluded_quality for c in customers)
    total_files = sum(c.files_total for c in customers)
    n_customers = len([c for c in customers if c.files_processed > 0])

    rows = [
        [
            Paragraph(f"{avg_green:.1f}%", styles["big_number"]),
            Paragraph(f"{total_green}", styles["big_number"]),
            Paragraph(f"{total_yellow}", styles["big_number"]),
            Paragraph(f"{total_red}", styles["big_number"]),
        ],
        [
            Paragraph("Durchschn. Gruen-Rate", styles["big_label"]),
            Paragraph("Felder Gruen (gesamt)", styles["big_label"]),
            Paragraph("Felder Gelb (gesamt)", styles["big_label"]),
            Paragraph("Felder Rot (gesamt)", styles["big_label"]),
        ],
        [
            Paragraph(
                f"{n_customers} Kunden / {included} KPI-Dateien ({processed} verarbeitet) von {total_files}",
                styles["big_label"],
            ),
            Paragraph(f"von {total_scored} bewerteten", styles["big_label"]),
            Paragraph("Vorschlag, Review noetig", styles["big_label"]),
            Paragraph("Manuell noetig", styles["big_label"]),
        ],
    ]

    col_w = 64 * mm
    table = Table(rows, colWidths=[col_w] * 4)

    ts = TableStyle(
        [
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, COLOR_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("BACKGROUND", (0, 0), (0, -1), COLOR_GREEN_BG),
            ("BACKGROUND", (1, 0), (1, -1), COLOR_GREEN_BG),
            ("BACKGROUND", (2, 0), (2, -1), COLOR_YELLOW_BG),
            ("BACKGROUND", (3, 0), (3, -1), COLOR_RED_BG),
        ]
    )
    table.setStyle(ts)
    return table


def _build_customer_overview_table(
    customers: list[CustomerSummary], styles
) -> LongTable:
    """Table: one row per customer with counts, percentages, and ampel bar."""
    hdr = styles["table_header"]
    cell = styles["table_cell"]
    cell_b = styles["table_cell_bold"]

    header_row = [
        Paragraph("Kunde", hdr),
        Paragraph("Dateien", hdr),
        Paragraph("Gruen", hdr),
        Paragraph("Gelb", hdr),
        Paragraph("Rot", hdr),
        Paragraph("Gesamt", hdr),
        Paragraph("% Gruen", hdr),
        Paragraph("Ampel-Verteilung", hdr),
        Paragraph("Anmerkung", hdr),
    ]

    rows: list[list] = [header_row]
    for c in customers:
        if c.scored_total == 0:
            if c.files_excluded_quality > 0 and c.files_failed == 0:
                note = "Datei(en) zu schlecht - aus KPI ausgeschlossen"
            else:
                note = "Parsing fehlgeschlagen" if c.files_failed > 0 else "Keine Daten"
            rows.append(
                [
                    Paragraph(escape(c.name), cell_b),
                    Paragraph(str(c.files_total), cell),
                    Paragraph("-", cell),
                    Paragraph("-", cell),
                    Paragraph("-", cell),
                    Paragraph("-", cell),
                    Paragraph("-", cell),
                    Paragraph("", cell),
                    Paragraph(escape(note), cell),
                ]
            )
            continue

        note_parts = []
        if c.files_failed > 0:
            note_parts.append(f"{c.files_failed} Datei(en) fehlgeschlagen")
        if c.files_excluded_quality > 0:
            note_parts.append(
                f"{c.files_excluded_quality} Datei(en) zu schlecht (nicht in KPI)"
            )
        if c.green_pct >= 50:
            note_parts.append("Gute Automatisierung")
        elif c.green_pct >= 25:
            note_parts.append("Teilautomatisierung")
        else:
            note_parts.append("Komplexes Format")

        rows.append(
            [
                Paragraph(escape(c.name), cell_b),
                Paragraph(f"{c.files_processed}/{c.files_total}", cell),
                Paragraph(f"<font color='#28a745'><b>{c.green_total}</b></font>", cell),
                Paragraph(
                    f"<font color='#b8860b'><b>{c.yellow_total}</b></font>", cell
                ),
                Paragraph(f"<font color='#dc3545'><b>{c.red_total}</b></font>", cell),
                Paragraph(str(c.scored_total), cell),
                Paragraph(f"<b>{c.green_pct:.1f}%</b>", cell_b),
                AmpelBalken(c.green_pct, c.yellow_pct, c.red_pct, width=120, height=12),
                Paragraph(escape("; ".join(note_parts)), cell),
            ]
        )

    # Totals row
    total_green = sum(c.green_total for c in customers)
    total_yellow = sum(c.yellow_total for c in customers)
    total_red = sum(c.red_total for c in customers)
    total_scored = sum(c.scored_total for c in customers)
    avg_green = total_green / total_scored * 100 if total_scored else 0

    rows.append(
        [
            Paragraph("<b>GESAMT</b>", cell_b),
            Paragraph(
                f"<b>{sum(c.files_processed - c.files_excluded_quality for c in customers)}/{sum(c.files_total for c in customers)}</b>",
                cell_b,
            ),
            Paragraph(f"<b>{total_green}</b>", cell_b),
            Paragraph(f"<b>{total_yellow}</b>", cell_b),
            Paragraph(f"<b>{total_red}</b>", cell_b),
            Paragraph(f"<b>{total_scored}</b>", cell_b),
            Paragraph(f"<b>{avg_green:.1f}%</b>", cell_b),
            AmpelBalken(
                avg_green,
                total_yellow / total_scored * 100 if total_scored else 0,
                total_red / total_scored * 100 if total_scored else 0,
                width=120,
                height=12,
            ),
            Paragraph("", cell),
        ]
    )

    table = LongTable(
        rows,
        colWidths=[
            38 * mm,
            16 * mm,
            16 * mm,
            16 * mm,
            16 * mm,
            18 * mm,
            18 * mm,
            48 * mm,
            70 * mm,
        ],
        repeatRows=1,
    )

    ts = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.35, COLOR_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            # Totals row background
            (
                "BACKGROUND",
                (0, len(rows) - 1),
                (-1, len(rows) - 1),
                colors.HexColor("#e8ecf0"),
            ),
            (
                "LINEABOVE",
                (0, len(rows) - 1),
                (-1, len(rows) - 1),
                1.2,
                COLOR_TEXT_DARK,
            ),
        ]
    )

    # Color the green % column per row
    for row_idx, c in enumerate(customers, start=1):
        if c.scored_total > 0:
            if c.green_pct >= 50:
                ts.add("BACKGROUND", (6, row_idx), (6, row_idx), COLOR_GREEN_BG)
            elif c.green_pct >= 25:
                ts.add("BACKGROUND", (6, row_idx), (6, row_idx), COLOR_YELLOW_BG)
            else:
                ts.add("BACKGROUND", (6, row_idx), (6, row_idx), COLOR_RED_BG)

    table.setStyle(ts)
    return table


def _build_grouped_detail_table(customers: list[CustomerSummary], styles) -> LongTable:
    """Per-customer grouped detail table: customer header row + file rows."""
    hdr = styles["table_header"]
    cell = styles["table_cell"]
    cell_b = styles["table_cell_bold"]

    COL_WIDTHS = [
        82 * mm,
        16 * mm,
        18 * mm,
        18 * mm,
        18 * mm,
        18 * mm,
        20 * mm,
        66 * mm,
    ]

    header_row = [
        Paragraph("Dateiname / Kunde", hdr),
        Paragraph("Status", hdr),
        Paragraph("Gruen", hdr),
        Paragraph("Gelb", hdr),
        Paragraph("Rot", hdr),
        Paragraph("Gesamt", hdr),
        Paragraph("% Gruen", hdr),
        Paragraph("Ampel / Anmerkung", hdr),
    ]

    rows: list[list] = [header_row]
    customer_header_indices: list[int] = []
    failed_file_indices: list[int] = []
    green_cell_indices: list[int] = []
    yellow_cell_indices: list[int] = []

    for cs in customers:
        # -- Customer header row --
        customer_header_indices.append(len(rows))
        if cs.scored_total > 0:
            rows.append(
                [
                    Paragraph(f"<b>{escape(cs.name)}</b>", cell_b),
                    Paragraph(
                        f"{cs.files_processed - cs.files_excluded_quality}/{cs.files_total}",
                        cell_b,
                    ),
                    Paragraph(
                        f"<font color='#28a745'><b>{cs.green_total}</b></font>", cell_b
                    ),
                    Paragraph(
                        f"<font color='#b8860b'><b>{cs.yellow_total}</b></font>", cell_b
                    ),
                    Paragraph(
                        f"<font color='#dc3545'><b>{cs.red_total}</b></font>", cell_b
                    ),
                    Paragraph(f"<b>{cs.scored_total}</b>", cell_b),
                    Paragraph(f"<b>{cs.green_pct:.1f}%</b>", cell_b),
                    AmpelBalken(
                        cs.green_pct, cs.yellow_pct, cs.red_pct, width=130, height=11
                    ),
                ]
            )
        else:
            if cs.files_excluded_quality > 0 and cs.files_failed == 0:
                note = "Datei(en) zu schlecht - aus KPI ausgeschlossen"
            else:
                note = (
                    "Parsing fehlgeschlagen" if cs.files_failed > 0 else "Keine Daten"
                )
            rows.append(
                [
                    Paragraph(f"<b>{escape(cs.name)}</b>", cell_b),
                    Paragraph(f"0/{cs.files_total}", cell_b),
                    Paragraph("-", cell_b),
                    Paragraph("-", cell_b),
                    Paragraph("-", cell_b),
                    Paragraph("-", cell_b),
                    Paragraph("-", cell_b),
                    Paragraph(escape(note), cell),
                ]
            )

        # -- Individual file rows --
        sorted_files = sorted(
            cs.file_results,
            key=lambda r: (0 if r.processed else 1, -(r.green_pct or -1)),
        )
        for item in sorted_files:
            row_idx = len(rows)
            if item.processed and item.include_in_kpi:
                pct_str = (
                    f"{item.green_pct:.1f}%" if item.green_pct is not None else "-"
                )
                note_str = item.note or ""
                if item.green_pct is not None and item.green_pct >= 50:
                    green_cell_indices.append(row_idx)
                elif item.green_pct is not None and item.green_pct >= 25:
                    yellow_cell_indices.append(row_idx)
                rows.append(
                    [
                        Paragraph(f"&nbsp;&nbsp;{escape(item.display_name)}", cell),
                        Paragraph("OK", cell),
                        Paragraph(
                            f"<font color='#28a745'>{item.green_count}</font>", cell
                        ),
                        Paragraph(
                            f"<font color='#b8860b'>{item.yellow_count}</font>", cell
                        ),
                        Paragraph(
                            f"<font color='#dc3545'>{item.red_count}</font>", cell
                        ),
                        Paragraph(str(item.total_scored), cell),
                        Paragraph(pct_str, cell),
                        Paragraph(escape(note_str), cell),
                    ]
                )
            elif item.processed and not item.include_in_kpi:
                rows.append(
                    [
                        Paragraph(f"&nbsp;&nbsp;{escape(item.display_name)}", cell),
                        Paragraph("Datei zu schlecht", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph(escape(item.note or "Nicht in KPI"), cell),
                    ]
                )
            else:
                failed_file_indices.append(row_idx)
                rows.append(
                    [
                        Paragraph(f"&nbsp;&nbsp;{escape(item.display_name)}", cell),
                        Paragraph("Fehler", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph("-", cell),
                        Paragraph(escape(item.note or "Nicht verarbeitet"), cell),
                    ]
                )

    table = LongTable(rows, colWidths=COL_WIDTHS, repeatRows=1)

    ts = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("LEADING", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.35, COLOR_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )

    # Customer header rows: light blue background
    for idx in customer_header_indices:
        ts.add("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#dce6f0"))
        ts.add("LINEABOVE", (0, idx), (-1, idx), 0.8, COLOR_TEXT_DARK)

    # Failed file rows
    for idx in failed_file_indices:
        ts.add("BACKGROUND", (0, idx), (-1, idx), COLOR_RED_BG)

    # Green % cell highlighting
    for idx in green_cell_indices:
        ts.add("BACKGROUND", (6, idx), (6, idx), COLOR_GREEN_BG)
    for idx in yellow_cell_indices:
        ts.add("BACKGROUND", (6, idx), (6, idx), COLOR_YELLOW_BG)

    table.setStyle(ts)
    return table


def _build_fazit(customers: list[CustomerSummary], styles) -> list:
    """Build the Fazit/recommendation section."""
    elements = []
    total_green = sum(c.green_total for c in customers)
    total_yellow = sum(c.yellow_total for c in customers)
    total_red = sum(c.red_total for c in customers)
    total_scored = sum(c.scored_total for c in customers)
    avg_green = total_green / total_scored * 100 if total_scored else 0
    excluded_quality_files = sum(c.files_excluded_quality for c in customers)
    processed_customers = [c for c in customers if c.files_processed > 0]
    n_good = len([c for c in processed_customers if c.green_pct >= 40])
    n_medium = len([c for c in processed_customers if 20 <= c.green_pct < 40])
    n_hard = len([c for c in processed_customers if c.green_pct < 20])

    elements.append(Paragraph("Fazit und Empfehlung", styles["section"]))
    elements.append(Spacer(1, 4))

    lines = []
    lines.append(
        f"Von <b>{total_scored}</b> bewerteten Feldern konnten <b>{total_green}</b> "
        f"(<b>{avg_green:.1f}%</b>) automatisch mit hoher Konfidenz uebernommen werden (Gruen). "
        f"<b>{total_yellow}</b> Felder ({total_yellow / total_scored * 100:.1f}%) "
        f"haben einen Vorschlag der geprueft werden muss (Gelb). "
        f"<b>{total_red}</b> Felder ({total_red / total_scored * 100:.1f}%) "
        f"muessen manuell bearbeitet werden (Rot)."
    )

    if n_good > 0:
        lines.append(
            f"<br/><br/><b>{n_good} Kunde(n)</b> zeigen eine gute Automatisierungsrate (>=40% Gruen). "
            f"Fuer diese Kunden ist eine signifikante Zeitersparnis bei der Stuecklistenuebertragung zu erwarten."
        )
    if n_medium > 0:
        lines.append(
            f"<br/><b>{n_medium} Kunde(n)</b> zeigen eine moderate Rate (20-40% Gruen) — "
            f"teilautomatisiert, manueller Review noetig."
        )
    if n_hard > 0:
        lines.append(
            f"<br/><b>{n_hard} Kunde(n)</b> haben komplexe Formate (&lt;20% Gruen). "
            f"Hier ist der manuelle Aufwand hoeher, aber Gelb-Vorschlaege unterstuetzen den Prozess."
        )

    lines.append(
        "<br/><br/><b>Wichtig:</b> Gruen bedeutet, dass das System sich bei diesen Feldern sicher ist "
        "und sie automatisch uebernommen werden koennen — ohne Nachkontrolle. "
        "Gelb-Felder werden als Vorschlag angezeigt und muessen kurz geprueft werden. "
        "Rot-Felder werden nicht automatisch befuellt."
    )

    lines.append(
        "<br/><br/><b>Hinweis zur Gruen-Rate:</b> Das System ist bewusst konservativ eingestellt. "
        "Es werden nur Felder als Gruen markiert, bei denen eine dreifache Verifizierung "
        "(Extraktion, Transformation, Gegencheck) erfolgreich war. "
        "Lieber ein Feld zu viel Gelb als ein falsches Gruen."
    )

    total_guaranteed = sum(c.guaranteed_files for c in customers)
    total_processed = sum(c.files_processed for c in customers)
    if total_processed > 0:
        lines.append(
            f"<br/><br/><b>Zero-Data-Loss:</b> Bei <b>{total_guaranteed}</b> von "
            f"{total_processed} verarbeiteten Dateien ist die Vollstaendigkeit "
            "<b>deterministisch garantiert</b> — jede Position aus dem PDF erscheint "
            "im Ergebnis (Text-Layer-Rekonstruktion mit Koordinaten-Anker). Keine "
            "Position kann still verloren gehen."
        )

    if excluded_quality_files > 0:
        lines.append(
            f'<br/><br/><b>Transparenz:</b> {excluded_quality_files} Datei(en) wurden als "zu schlecht" '
            "klassifiziert und nicht in die KPI-Berechnung (Gruen/Gelb/Rot) einbezogen."
        )

    for line in lines:
        elements.append(Paragraph(line, styles["body"]))

    return elements


def create_pdf_report(customers: list[CustomerSummary], output_path: Path) -> None:
    styles = build_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )

    story: list = []

    # --- Page 1: Title + KPIs + Customer overview ---
    story.append(
        Paragraph(
            "POC-Auswertung: KI-Stuecklistenagent",
            styles["title"],
        )
    )
    story.append(
        Paragraph(
            f"Erstellt: {RUN_AT.strftime('%d.%m.%Y %H:%M')} &nbsp;|&nbsp; "
            f"Fuer: Schaufler Tooling GmbH &amp; Co. KG &nbsp;|&nbsp; "
            f"Von: Prozessia",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph("Zusammenfassung", styles["section"]))
    story.append(_build_kpi_boxes(customers, styles))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Ergebnis pro Kunde", styles["section"]))
    story.append(_build_customer_overview_table(customers, styles))

    # --- Page 2: Fazit ---
    story.append(PageBreak())
    story.extend(_build_fazit(customers, styles))

    # --- Page 3+: Grouped detail (per customer + per file) ---
    story.append(PageBreak())
    story.append(Paragraph("Detailtabelle: Kunde &rarr; Dateien", styles["section"]))
    story.append(
        Paragraph(
            "Pro Kunde die Gesamtwerte, darunter jede einzelne Datei mit Gruen/Gelb/Rot-Verteilung.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 4))
    if customers:
        story.append(_build_grouped_detail_table(customers, styles))
    else:
        story.append(
            Paragraph(
                "Keine PDF-Dateien unter data/input gefunden.",
                styles["body"],
            )
        )

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)


def _draw_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#486581"))
    canvas.drawString(
        document.leftMargin,
        7 * mm,
        f"POC-Auswertung — {RUN_AT.strftime('%d.%m.%Y %H:%M')} — Prozessia fuer Schaufler Tooling",
    )
    canvas.drawRightString(
        document.pagesize[0] - document.rightMargin,
        7 * mm,
        f"Seite {canvas.getPageNumber()}",
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------


async def process_file(filepath: Path, index: int, total: int) -> FileRunResult:
    display_name = display_name_for(filepath)
    customer = get_customer_name(filepath)
    print(f"  [{index}/{total}] {customer}: {filepath.name} ...")

    job_id = uuid.uuid4().hex[:12]
    job_store.create(job_id, filepath.name, filepath)

    timeout_note: str | None = None
    try:
        await asyncio.wait_for(run_pipeline(job_id), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        timeout_note = f"Timeout nach {TIMEOUT_SECONDS // 60} Min."
        job = job_store.get(job_id)
        current_progress = job.progress if job is not None else 0.0
        job_store.update(
            job_id,
            status="failed",
            progress=current_progress,
            error=timeout_note,
        )
    except Exception as exc:  # noqa: BLE001
        job_store.update(job_id, status="failed", error=f"Fehler: {exc}")

    job = job_store.get(job_id)
    if job is None or job.status != "completed" or job.audit is None:
        return FileRunResult(
            display_name=display_name,
            customer=customer,
            processed=False,
            include_in_kpi=False,
            green_count=0,
            yellow_count=0,
            red_count=0,
            neutral_count=0,
            total_scored=0,
            green_pct=None,
            yellow_pct=None,
            red_pct=None,
            total_fields=None,
            note=normalize_note(
                timeout_note
                or (job.error if job else "Job nicht geladen")
                or "Nicht verarbeitet"
            ),
        )

    audit = job.audit
    include_in_kpi = _is_kpi_eligible(audit)
    note = ""
    if not include_in_kpi:
        note = "Datei zu schlecht / nicht robust parsebar - aus KPI ausgeschlossen"
    elif audit.completeness_guaranteed:
        # RB-1: deterministic text-layer reconstruction → every position guaranteed.
        note = "Vollstaendigkeit garantiert (deterministisch)"
    elif audit.guard_basis == "row_count_fallback":
        note = "Vollstaendigkeit nicht ueber Positionen garantiert"

    return FileRunResult(
        display_name=display_name,
        customer=customer,
        processed=True,
        include_in_kpi=include_in_kpi,
        green_count=audit.green_count,
        yellow_count=audit.yellow_count,
        red_count=audit.red_count,
        neutral_count=audit.neutral_count,
        total_scored=audit.total_scored,
        green_pct=round(audit.green_pct, 1),
        yellow_pct=round(audit.yellow_pct, 1),
        red_pct=round(audit.red_pct, 1),
        total_fields=audit.total_cells,
        note=note,
        completeness_guaranteed=audit.completeness_guaranteed,
        guard_basis=audit.guard_basis,
    )


async def process_file_limited(
    filepath: Path,
    index: int,
    total: int,
    semaphore: asyncio.Semaphore,
) -> FileRunResult:
    async with semaphore:
        try:
            return await process_file(filepath, index, total)
        except Exception as exc:  # noqa: BLE001
            return FileRunResult(
                display_name=display_name_for(filepath),
                customer=get_customer_name(filepath),
                processed=False,
                include_in_kpi=False,
                green_count=0,
                yellow_count=0,
                red_count=0,
                neutral_count=0,
                total_scored=0,
                green_pct=None,
                yellow_pct=None,
                red_pct=None,
                total_fields=None,
                note=normalize_note(f"Abgebrochen: {exc}"),
            )


async def main() -> None:
    files = find_pdf_files(INPUT_DIR)
    print(f"\n{'='*60}")
    print("  POC-Auswertung: Stuecklistenagent")
    print(f"  {len(files)} PDF-Dateien gefunden")
    print(f"  Timeout: {TIMEOUT_MINUTES} Min. | Parallel: {PARALLEL_WORKERS}")
    print(f"{'='*60}\n")

    if not files:
        print("Keine PDF-Dateien in data/input/ gefunden. Abbruch.")
        return

    semaphore = asyncio.Semaphore(max(1, min(PARALLEL_WORKERS, len(files))))
    tasks = [
        asyncio.create_task(process_file_limited(filepath, idx, len(files), semaphore))
        for idx, filepath in enumerate(files, start=1)
    ]
    results: list[FileRunResult] = list(await asyncio.gather(*tasks))

    customers = aggregate_by_customer(results)

    # Print console summary
    print(f"\n{'='*60}")
    print("  ERGEBNIS")
    print(f"{'='*60}")
    total_scored = sum(c.scored_total for c in customers)
    total_green = sum(c.green_total for c in customers)
    total_yellow = sum(c.yellow_total for c in customers)
    total_red = sum(c.red_total for c in customers)
    total_excluded_quality = sum(c.files_excluded_quality for c in customers)

    for c in customers:
        if c.scored_total > 0:
            print(
                f"  {c.name:25s}  Gruen: {c.green_total:5d} ({c.green_pct:5.1f}%)  "
                f"Gelb: {c.yellow_total:5d} ({c.yellow_pct:5.1f}%)  "
                f"Rot: {c.red_total:5d} ({c.red_pct:5.1f}%)"
            )
        elif c.files_processed > 0 and c.files_excluded_quality > 0:
            print(f"  {c.name:25s}  -- verarbeitet, aber aus KPI ausgeschlossen --")
        elif c.files_failed > 0:
            print(f"  {c.name:25s}  -- Parsing fehlgeschlagen --")
        else:
            print(f"  {c.name:25s}  -- keine Daten --")

    if total_scored > 0:
        print(
            f"\n  {'GESAMT':25s}  Gruen: {total_green:5d} ({total_green/total_scored*100:5.1f}%)  "
            f"Gelb: {total_yellow:5d} ({total_yellow/total_scored*100:5.1f}%)  "
            f"Rot: {total_red:5d} ({total_red/total_scored*100:5.1f}%)"
        )
    if total_excluded_quality > 0:
        print(
            f"\n  Hinweis: {total_excluded_quality} Datei(en) als 'zu schlecht' aus KPI ausgeschlossen"
        )

    total_guaranteed = sum(c.guaranteed_files for c in customers)
    total_processed = sum(c.files_processed for c in customers)
    if total_processed > 0:
        print(
            f"  Zero-Data-Loss: {total_guaranteed}/{total_processed} Datei(en) "
            "mit deterministisch garantierter Vollstaendigkeit"
        )
    print()

    create_pdf_report(customers, OUTPUT_PDF)
    print(f"PDF gespeichert: {OUTPUT_PDF}")


if __name__ == "__main__":
    # Parse --timeout from CLI
    for i, arg in enumerate(sys.argv):
        if arg == "--timeout" and i + 1 < len(sys.argv):
            try:
                TIMEOUT_MINUTES = int(sys.argv[i + 1])
                TIMEOUT_SECONDS = TIMEOUT_MINUTES * 60
            except ValueError:
                pass

    try:
        asyncio.run(main())
    finally:
        try:
            job_store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            TEMP_DB_PATH.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
