"""Coordinate Table Reconstructor (RB-1) — deterministic text-layer BOM parsing.

Replaces the LLM-segmented text path (parse_pdf_text) for born-digital PDFs.
Such PDFs carry exact glyph coordinates; the table is reconstructed by clustering
words into spatial row bands and assigning each word to a column corridor by its
x-position. The LLM never decides row/column structure — only semantic column
labels (one call per document) and, later, explicitly flagged ambiguous cells.

Completeness anchor: ``pdf_row_bands`` (every data-band id) is computed BEFORE any
LLM call, so a row the LLM might later collapse or drop is still counted. This is
what makes T-007 (N parts under one position number) and the nameless-row case
detectable instead of silent — row identity is the spatial band, never the
position value.

The deterministic core implemented here is intentionally simple and well-tested
in isolation (see tests/test_coordinate_table.py). The heuristics marked NOTE
D1–D5 are where tuning against the 19 POC PDFs + the 500-position real BOM is
required before this path replaces parse_pdf_text in production.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from src.core.models import (
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceMetadata,
)
from src.ingestion.file_router import infer_customer
from src.ingestion.pdf_common import ExtractionError, open_pdf_document
from src.llm.base import BaseLLM

logger = logging.getLogger(__name__)

# --- Tuning constants (single place; later YAML-driven, no code change per customer) ---
# Geometric page-edge trim. KEPT SMALL ON PURPOSE: a larger margin silently drops
# real data rows that sit near the page bottom (TCG had genuine items 53/54 in the
# old 8% band) — invisible to the completeness anchor, which is the cardinal sin.
# Top metadata is handled structurally (pre-header drop); any footer that survives
# this 2% trim becomes a RED junk row (over-reporting), the safe direction.
_HEADER_FOOTER_MARGIN = 0.02   # fraction of page height trimmed top & bottom
_MIN_BAND_TOLERANCE = 3.0      # floor for y-band grouping (PDF points)
_BAND_TOLERANCE_RATIO = 0.6    # band tolerance = ratio * median glyph height
_CORRIDOR_PAD = 4.0            # x-corridor padding when assigning a word
_HEADER_CLUSTER_TOL = 20.0    # x-tolerance: header words within this share one column
# A word is "boundary-ambiguous" when the runner-up column anchor is nearly as
# close as the assigned one (d_assigned >= ratio * d_runnerup). Such cells must
# NOT be auto-GREEN — the deterministic value is read correctly but its COLUMN is
# uncertain, the one error mode self-referential Check-2 cannot catch. They are
# capped to YELLOW via extraction_uncertain_cells. Closes the free-text gap.
# Deliberately tight (0.90): only words that are NEARLY equidistant between two
# column anchors are flagged. Typed fields are already protected by rule
# validation; this guards the free-text columns without demoting confident cells.
_AMBIGUITY_RATIO = 0.90
_MULTILINE_LOOKAHEAD = 8       # NOTE D1: max continuation bands folded into one anchor
_MIN_COLS = 3
_MIN_DATA_ROWS = 2

# Column-type keyword hints — used ONLY to (a) detect a header band and (b) find the
# position / description corridors for the multiline-merge rule. Not a mapping.
_POSITION_KW = {"pos", "position", "detail", "pozice", "nr", "no", "num", "index"}
_DESCRIPTION_KW = {
    "benennung", "bezeichnung", "description", "popis", "name", "teil",
    "denominazione", "désignation",
}
_HEADER_KW = _POSITION_KW | _DESCRIPTION_KW | {
    "stk", "stück", "stck", "qty", "quantity", "anzahl", "menge", "množství",
    "werkst", "werkstoff", "material", "matériau", "fertigma", "dimension",
    "maße", "masse", "rohmass", "abmaß", "rozměr", "härte", "hardness", "hrc",
}

# Page-marker footer/header lines ("… Seite 3 …", "Blatt 1 von 3", "Page 2 of 5").
# Content-based, multilingual. A BOM data row never contains "<page-word> <number>",
# so dropping these cannot lose a real row — unlike a geometric margin, which did.
_PAGE_MARKER_RE = re.compile(
    r"\b(seite|blatt|page|strana|pagina|feuille|sheet|list)\s+\d+",
    re.IGNORECASE,
)


# ===================================================================
# Data carriers
# ===================================================================


@dataclass(frozen=True)
class Word:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1.0)


@dataclass
class RowBand:
    """One deterministic spatial row — the unit of row identity."""

    band_id: str            # "p{page}:b{idx:04d}" — stable, assigned pre-LLM
    page: int
    words: list[Word]
    y0: float
    y1: float
    is_header: bool = False
    is_continuation: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in sorted(self.words, key=lambda w: w.x0))


@dataclass
class ColumnCorridor:
    name: str               # raw header token (semantic label added later)
    x_left: float
    x_right: float
    center: float           # anchor x (nearest-anchor assignment)

    def contains(self, x_center: float) -> bool:
        return (self.x_left - _CORRIDOR_PAD) <= x_center <= (self.x_right + _CORRIDOR_PAD)


@dataclass
class TableSection:
    """A contiguous block sharing one column layout (NOTE D3: per-section, not global)."""

    corridors: list[ColumnCorridor] = field(default_factory=list)
    data_bands: list[RowBand] = field(default_factory=list)


# ===================================================================
# Public entry point — drop-in for parse_pdf_text in structure_normalizer
# ===================================================================


async def reconstruct_table(filepath: Path | str, llm: BaseLLM) -> ParsedBOM:
    filepath = Path(filepath)
    words = _extract_words(filepath)
    if not words:
        raise ExtractionError(f"No text-layer words in {filepath.name}")

    bands = _cluster_into_bands(words)
    sections = _segment_into_sections(bands)
    rows, row_keys, locations = _assign_cells(sections)
    rows, row_keys, locations = _merge_multiline(rows, row_keys, locations, sections)

    # INDEPENDENT completeness anchor — every data band, before any LLM touches it.
    pdf_row_bands = [
        b.band_id
        for s in sections
        for b in s.data_bands
        if not b.is_header and not b.is_continuation
    ]

    headers = _raw_column_labels(sections)

    if not _reconstruction_reliable(sections) or len(rows) < _MIN_DATA_ROWS:
        # The deterministic path could not find a trustworthy tabular header
        # (e.g. GF's rotated/transposed matrix layout, where the "header" collapses
        # to repeated tokens). Decline honestly so structure_normalizer falls back
        # to the Vision path instead of emitting confident garbage. ZDL-1 spirit.
        raise ExtractionError(
            f"No reliable BOM table structure in {filepath.name}: "
            f"{len(set(headers))} distinct columns / {len(rows)} rows — "
            f"declining deterministic path (Vision fallback)."
        )

    headers = await _label_columns_semantically(headers, rows, llm)

    page_texts = _document_text_pages(bands)

    # Boundary-ambiguous cells → capped to YELLOW downstream (closes the free-text
    # column-misassignment false-green gap). Keyed by the same row index transform
    # assigns via enumerate(rows).
    uncertain_cells = {
        i: cols
        for i, loc in enumerate(locations)
        if (cols := [
            name
            for name, cell in loc.items()
            if cell.get("match_type") == "column_boundary"
        ])
    }

    return ParsedBOM(
        source=SourceMetadata(
            filename=filepath.name,
            filepath=str(filepath),
            customer=infer_customer(filepath),
            format=FileFormat.PDF,
            pages=_page_count(filepath),
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.97,
        ),
        headers=headers,
        rows=rows,
        row_keys=row_keys,
        pdf_row_bands=pdf_row_bands,
        expected_position_count=len(pdf_row_bands),
        metadata={
            "extraction_method": "coordinate_table_deterministic",
            "has_text_layer": True,
            "row_band_count": len(pdf_row_bands),
            # Check-2 evidence: exact per-cell page+bbox+text from the coordinate
            # reconstruction — the value literally IS the text-layer content at
            # that location, so the scorer can certify GREEN deterministically.
            "source_locations": {i: loc for i, loc in enumerate(locations)},
            "document_text_pages": page_texts,
            "document_text_layer": _serialize_pages(page_texts),
            "check2_reason": "text_layer_direct",
            "llm_uncertain_cells": uncertain_cells,
        },
    )


def _document_text_pages(bands: list[RowBand]) -> list[str]:
    """Per-page text (one band per line) for the scorer's anchor-search fallback."""
    pages: dict[int, list[str]] = {}
    for band in bands:
        text = band.text.strip()
        if text:
            pages.setdefault(band.page, []).append(text)
    return ["\n".join(pages[p]) for p in sorted(pages)]


def _serialize_pages(page_texts: list[str]) -> str:
    return "\n\n--- PAGE BREAK ---\n\n".join(t for t in page_texts if t)


# ===================================================================
# Deterministic steps
# ===================================================================


def _extract_words(filepath: Path) -> list[Word]:
    """page.get_text('words') → Word list, C1 header/footer band dropped."""
    doc = open_pdf_document(filepath)
    out: list[Word] = []
    try:
        for page_idx, page in enumerate(doc):
            page_height = float(getattr(page.rect, "height", 0.0) or 0.0)
            top_cut = _HEADER_FOOTER_MARGIN * page_height
            bottom_cut = (1.0 - _HEADER_FOOTER_MARGIN) * page_height
            for entry in page.get_text("words"):
                x0, y0, x1, y1, token = entry[0], entry[1], entry[2], entry[3], entry[4]
                token = str(token).strip()
                if not token:
                    continue
                if page_height > 0:
                    center_y = (float(y0) + float(y1)) / 2
                    if center_y < top_cut or center_y > bottom_cut:
                        continue  # C1: page number / revision stamp / title
                out.append(
                    Word(page_idx, float(x0), float(y0), float(x1), float(y1), token)
                )
    finally:
        doc.close()
    return out


def _cluster_into_bands(words: list[Word]) -> list[RowBand]:
    """Cluster words into page-local y-bands; assign the stable band_id.

    NOTE D4: the tolerance is derived from the per-document median glyph height
    (not a fixed constant) so glyph-split exporters with tiny boxes still group.
    """
    if not words:
        return []

    med_h = median(w.height for w in words)
    tol = max(_MIN_BAND_TOLERANCE, med_h * _BAND_TOLERANCE_RATIO)

    bands: list[RowBand] = []
    per_page_index: dict[int, int] = {}

    # Sort by (page, y-center, x) so band growth is top-to-bottom, left-to-right.
    for word in sorted(words, key=lambda w: (w.page, w.cy, w.x0)):
        current = bands[-1] if bands else None
        if (
            current is not None
            and current.page == word.page
            and abs(word.cy - (current.y0 + current.y1) / 2) <= tol
        ):
            current.words.append(word)
            current.y0 = min(current.y0, word.y0)
            current.y1 = max(current.y1, word.y1)
            continue

        idx = per_page_index.get(word.page, 0)
        per_page_index[word.page] = idx + 1
        bands.append(
            RowBand(
                band_id=f"p{word.page}:b{idx:04d}",
                page=word.page,
                words=[word],
                y0=word.y0,
                y1=word.y1,
            )
        )
    return bands


def _segment_into_sections(bands: list[RowBand]) -> list[TableSection]:
    """Split bands into sections that share ONE column layout.

    NOTE D3 (stacked headers): a real BOM header can span several lines (e.g.
    "Nr. Material/ Härte …" / "Pos. Anzahl Rm Masse" / "Modellname Bezeichnung
    …"). Consecutive header bands on one page are folded into a single logical
    header; corridors come from the MOST GRANULAR line (most words), which is the
    finest column grid. Each header run opens a section with its own corridors.

    NOTE D2: a header repeated on later pages starts a NEW section (per-section
    corridors) instead of leaking in as a data row. A header-detection miss on a
    page is tolerated — its data bands fall into the previous section, reusing the
    same form layout.

    A leading metadata/title block before any header (no column structure) is
    dropped at the end (it carries no BOM rows); BOM data bands always sit under a
    detected header, so this is not a silent-loss path.
    """
    # Drop page-marker footer/header lines by content (safe — see _PAGE_MARKER_RE).
    bands = [b for b in bands if not _is_page_marker_band(b)]

    # Group bands per page, preserving the top-to-bottom order from clustering.
    pages: dict[int, list[RowBand]] = {}
    page_order: list[int] = []
    for band in bands:
        if band.page not in pages:
            pages[band.page] = []
            page_order.append(band.page)
        pages[band.page].append(band)

    sections: list[TableSection] = []
    current: TableSection | None = None

    for page in page_order:
        page_bands = pages[page]
        candidate_idx = {i for i, b in enumerate(page_bands) if _is_header_band(b)}

        if not candidate_idx:
            # Continuation page (no header of its own): its rows belong to the
            # previous section's layout. Leading pages before any header (pure
            # title page) have no current section yet and are dropped.
            if current is not None:
                current.data_bands.extend(page_bands)
            continue

        # The real BOM header is the STRONGEST candidate on the page (most column
        # keywords); a title/banner block is always weaker on the same page. This
        # relative pick rejects banners without an absolute threshold that would
        # break sparse real headers (e.g. FCA's 3-keyword header).
        primary = max(
            candidate_idx, key=lambda i: (_kw_hits(page_bands[i]), len(page_bands[i].words))
        )

        # Absorb the consecutive stacked header lines around the primary (a real
        # multi-line header is contiguous; a banner is separated by other bands).
        # A neighbour joins only if it is itself header-like AND has >= 3 tokens,
        # so a 2-token banner ("Stücklistentyp GWB_AV-Stückliste") stays out.
        start = end = primary
        while (start - 1) in candidate_idx and len(page_bands[start - 1].words) >= 3:
            start -= 1
        while (end + 1) in candidate_idx and len(page_bands[end + 1].words) >= 3:
            end += 1

        run = page_bands[start : end + 1]
        for b in run:
            b.is_header = True
        current = TableSection(corridors=_infer_corridors(run))
        sections.append(current)

        # Bands before the header run are page-top metadata (dropped); bands after
        # are this page's data rows.
        current.data_bands.extend(page_bands[end + 1 :])

    return [s for s in sections if s.data_bands and s.corridors]


def _is_page_marker_band(band: RowBand) -> bool:
    """True for a page-number footer/header line (content-based, never a data row)."""
    return bool(_PAGE_MARKER_RE.search(band.text))


def _kw_hits(band: RowBand) -> int:
    """Count distinct column-keyword matches in a band (header strength signal)."""
    return sum(
        1
        for w in band.words
        if (t := _norm(w.text)) and any(kw in t for kw in _HEADER_KW)
    )


def _is_header_band(band: RowBand) -> bool:
    """A header band matches >= 2 column keywords AND is not value-dominated.

    The value-fraction guard is what stops a data row (full of part numbers,
    dimensions and material codes) from being mistaken for a header — the defect
    that fragmented the real ZF BOM into garbage sections.
    """
    tokens = [w.text for w in band.words]
    if len(tokens) < 2:
        return False
    norm = [_norm(t) for t in tokens]
    kw_hits = sum(1 for t in norm if t and any(kw in t for kw in _HEADER_KW))
    value_frac = sum(1 for t in tokens if _is_value_token(t)) / len(tokens)
    return kw_hits >= 2 and value_frac < 0.35


def _is_value_token(text: str) -> bool:
    """True for tokens that look like BOM *data* (number, dimension, code).

    Used only to reject value-dominated bands as headers — not for extraction.
    """
    t = text.strip()
    if not t:
        return False
    if re.match(r"^[Ø∅ø]?[0-9]", t):  # 12.198, 400x78x55, Ø289, 1-32, 47-49
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}[0-9][A-Za-z0-9.\-]*", t):  # S355J2G3, AA02182362, X38…
        return True
    if "_" in t and any(c.isdigit() for c in t):  # 1_5670_1_21_01-032_VERTEILERBLO
        return True
    return False


def _infer_corridors(header_bands: list[RowBand]) -> list[ColumnCorridor]:
    """Build column anchors from a (possibly multi-line) header by 2D merge.

    All header words across the stacked lines are clustered by x-center: words
    within _HEADER_CLUSTER_TOL share one column. Vertically stacked tokens
    ("Nr."/"Pos."/"Modellname"; "Härte"/"HRC"; "Rm"/"N/mm2") collapse into one
    anchor named after the bottom-most (finest) line. Columns that exist ONLY in
    an upper line ("Pos.", "Anzahl") still get their own anchor — this is what
    recovers the left-most position/quantity columns that a finest-line-only
    profile silently dropped (real data loss on the ZF pages 18-19).
    """
    words = sorted(
        (w for band in header_bands for w in band.words), key=lambda w: w.cx
    )
    if len(words) < 2:
        return []

    # Greedy x-clustering on running cluster mean.
    clusters: list[list[Word]] = []
    for word in words:
        if clusters:
            mean_cx = sum(w.cx for w in clusters[-1]) / len(clusters[-1])
            if abs(word.cx - mean_cx) <= _HEADER_CLUSTER_TOL:
                clusters[-1].append(word)
                continue
        clusters.append([word])

    # One anchor per cluster: position + name from the bottom-most (finest) word.
    anchors: list[tuple[float, str]] = []
    for cluster in clusters:
        bottom = max(cluster, key=lambda w: w.y0)
        anchors.append((bottom.cx, bottom.text))
    anchors.sort(key=lambda a: a[0])

    centers = [c for c, _ in anchors]
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    avg_gap = sum(gaps) / len(gaps) if gaps else 60.0

    corridors: list[ColumnCorridor] = []
    used_names: dict[str, int] = {}
    for i, (center, name) in enumerate(anchors):
        left = center - avg_gap / 2 if i == 0 else (centers[i - 1] + center) / 2
        right = (
            center + avg_gap / 2
            if i == len(anchors) - 1
            else (center + centers[i + 1]) / 2
        )
        # Disambiguate duplicate header labels so dict keys never collide.
        if name in used_names:
            used_names[name] += 1
            name = f"{name}_{used_names[name]}"
        else:
            used_names[name] = 1
        corridors.append(
            ColumnCorridor(name=name, x_left=left, x_right=right, center=center)
        )
    return corridors


def _assign_cells(
    sections: list[TableSection],
) -> tuple[list[dict[str, str | None]], list[str], list[dict[str, dict[str, object]]]]:
    """Each data band → one row dict; each word → column by x-corridor.

    THIS is where N same-position parts become N rows: identity is the band
    (band_id), never the position value. Returns (rows, row_keys, locations)
    aligned 1:1. ``locations[i][col]`` is the exact page+bbox+text of that cell —
    the deterministic Check-2 evidence the scorer needs to certify GREEN.
    """
    rows: list[dict[str, str | None]] = []
    row_keys: list[str] = []
    locations: list[dict[str, dict[str, object]]] = []

    for section in sections:
        corridors = section.corridors
        if not corridors:
            continue
        for band in section.data_bands:
            if band.is_continuation:
                continue
            row: dict[str, str | None] = {c.name: None for c in corridors}
            buckets: dict[str, list[Word]] = {c.name: [] for c in corridors}
            for word in band.words:
                # Nearest-anchor: every word gets a column — NO silent drop of
                # tokens that fall left of the first / right of the last corridor.
                corridor = _corridor_for(word.cx, corridors)
                buckets[corridor.name].append(word)
            corridor_by_name = {c.name: c for c in corridors}
            row_locations: dict[str, dict[str, object]] = {}
            for name, ws in buckets.items():
                if not ws:
                    continue
                sorted_ws = sorted(ws, key=lambda w: w.x0)
                row[name] = " ".join(w.text for w in sorted_ws)
                x_min = min(w.x0 for w in ws)
                x_max = max(w.x1 for w in ws)
                # Ambiguity on the whole cell's centre (not per word): a multi-word
                # value centred in its corridor is confident; only a value sitting
                # between two anchors is flagged.
                ambiguous = _word_assignment_ambiguous(
                    (x_min + x_max) / 2, corridor_by_name[name], corridors
                )
                row_locations[name] = {
                    "page": band.page + 1,
                    "bbox": [x_min, min(w.y0 for w in ws), x_max, max(w.y1 for w in ws)],
                    "text": row[name],
                    "match_type": (
                        "column_boundary" if ambiguous else "column_corridor"
                    ),
                }
            rows.append(row)
            row_keys.append(band.band_id)
            locations.append(row_locations)
    return rows, row_keys, locations


def _corridor_for(
    x_center: float, corridors: list[ColumnCorridor]
) -> ColumnCorridor:
    """Return the corridor whose anchor center is nearest to x_center."""
    return min(corridors, key=lambda c: abs(x_center - c.center))


def _word_assignment_ambiguous(
    x_center: float,
    assigned: ColumnCorridor,
    corridors: list[ColumnCorridor],
) -> bool:
    """True when the runner-up column anchor is nearly as close as the assigned one.

    Such a word sits in the no-man's-land between two columns; its column is not
    trustworthy enough to certify GREEN (the value is read correctly, but it could
    belong to the neighbouring field).
    """
    if len(corridors) < 2:
        return False
    d_assigned = abs(x_center - assigned.center)
    d_runner_up = min(
        abs(x_center - c.center) for c in corridors if c is not assigned
    )
    return d_assigned >= _AMBIGUITY_RATIO * d_runner_up


def _merge_multiline(
    rows: list[dict[str, str | None]],
    row_keys: list[str],
    locations: list[dict[str, dict[str, object]]],
    sections: list[TableSection],
) -> tuple[
    list[dict[str, str | None]], list[str], list[dict[str, dict[str, object]]]
]:
    """Fold pure description-overflow bands into the preceding anchor row.

    NOTE D1: the dangerous direction. The merge rule must NOT collapse distinct
    parts (that would re-create T-007) and must NOT eat nameless part rows (which
    have no position but ARE real rows). Rule: a band is a continuation ONLY when
    its sole non-empty cell is the description column. A nameless part row also
    populates qty / material / dimensions, so it is preserved. The cell's source
    location (text + bbox) is merged in parallel so Check-2 evidence stays aligned.
    """
    if not rows:
        return rows, row_keys, locations

    desc_names = _description_corridor_names(sections)
    band_by_id = {b.band_id: b for s in sections for b in s.data_bands}

    merged_rows: list[dict[str, str | None]] = []
    merged_keys: list[str] = []
    merged_locs: list[dict[str, dict[str, object]]] = []
    for row, key, loc in zip(rows, row_keys, locations, strict=True):
        non_empty = {k for k, v in row.items() if v and str(v).strip()}
        only_description = bool(desc_names) and bool(non_empty) and non_empty <= desc_names
        if only_description and merged_rows:
            anchor = merged_rows[-1]
            anchor_loc = merged_locs[-1]
            for name in non_empty:
                addition = str(row[name]).strip()
                anchor[name] = (
                    f"{anchor[name]} {addition}".strip()
                    if anchor.get(name)
                    else addition
                )
                if name in loc:
                    if name in anchor_loc:
                        prev = anchor_loc[name]["bbox"]
                        cur = loc[name]["bbox"]
                        anchor_loc[name]["bbox"] = [
                            min(prev[0], cur[0]),
                            min(prev[1], cur[1]),
                            max(prev[2], cur[2]),
                            max(prev[3], cur[3]),
                        ]
                    else:
                        anchor_loc[name] = dict(loc[name])
                    anchor_loc[name]["text"] = anchor[name]
            # Mark the folded band as a continuation so the completeness anchor
            # (pdf_row_bands) does not count it as a separate expected row —
            # keeps emitted rows == anchor on the deterministic path.
            cont = band_by_id.get(key)
            if cont is not None:
                cont.is_continuation = True
            continue
        merged_rows.append(dict(row))
        merged_keys.append(key)
        merged_locs.append(dict(loc))
    return merged_rows, merged_keys, merged_locs


def _description_corridor_names(sections: list[TableSection]) -> set[str]:
    names: set[str] = set()
    for section in sections:
        for corridor in section.corridors:
            if any(kw in _norm(corridor.name) for kw in _DESCRIPTION_KW):
                names.add(corridor.name)
    return names


def _section_header_reliable(corridors: list[ColumnCorridor]) -> bool:
    """A section's header is reliable when it has enough DISTINCT column labels.

    A genuine BOM header has several distinct labels. When the chosen header
    collapses to repeated tokens (GF's rotated matrix: HRC/HRC_2/HRC_3…), the
    de-dup suffixes inflate the count while the distinct base set stays tiny and
    the suffix fraction is high — the tell of a layout to decline.
    """
    names = [c.name for c in corridors]
    if len(names) < _MIN_COLS:
        return False
    bases = {re.sub(r"_\d+$", "", n) for n in names}
    dup_frac = sum(1 for n in names if re.search(r"_\d+$", n)) / len(names)
    return len(bases) >= _MIN_COLS and dup_frac < 0.5


def _reconstruction_reliable(sections: list[TableSection]) -> bool:
    """Row-weighted: decline only when MOST rows sit under degenerate headers.

    GF scores 0 % (every section degenerate) → declined → Vision fallback. Normal
    BOMs score ~100 %. The 50 % threshold tolerates a few odd sub-blocks without
    discarding a document the deterministic path otherwise handles well.
    """
    total = sum(len(s.data_bands) for s in sections)
    if total == 0:
        return False
    reliable = sum(
        len(s.data_bands)
        for s in sections
        if _section_header_reliable(s.corridors)
    )
    return reliable / total >= 0.5


def _raw_column_labels(sections: list[TableSection]) -> list[str]:
    """Header tokens in left-to-right order (union across sections, deduped)."""
    labels: list[str] = []
    for section in sections:
        for corridor in section.corridors:
            if corridor.name not in labels:
                labels.append(corridor.name)
    return labels


async def _label_columns_semantically(
    raw_headers: list[str],
    rows: list[dict[str, str | None]],
    llm: BaseLLM,
) -> list[str]:
    """Return the raw coordinate header tokens unchanged — by design.

    Ingestion on the deterministic path makes ZERO LLM calls (no cost, no 429
    surface). The downstream column mapper (`mapping.map_columns`) already resolves
    each column to the target schema using the header token PLUS sample values, so
    a separate header-canonicalisation call here would be redundant cost. The
    ``llm``/``rows`` parameters are kept for a future opt-in cleanup pass but are
    intentionally unused now. Never touches row/column structure → cannot lose data.
    """
    del rows, llm  # intentionally unused: ingestion stays deterministic and free
    return raw_headers


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def _page_count(filepath: Path) -> int:
    doc = open_pdf_document(filepath)
    try:
        return doc.page_count
    finally:
        doc.close()
