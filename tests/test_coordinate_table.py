"""RB-1 proof tests for the deterministic coordinate table reconstructor.

These exercise the deterministic core directly with stubbed word coordinates —
no PDF, no Azure. They prove that the new row-band identity solves the two bugs
that the position-keyed architecture cannot:

  * T-007: N distinct parts under ONE position number → N rows (not 1).
  * Nameless rows (no position number) → preserved as distinct rows.
"""

from __future__ import annotations

from src.ingestion.coordinate_table import (
    ColumnCorridor,
    RowBand,
    TableSection,
    Word,
    _assign_cells,
    _cluster_into_bands,
    _is_header_band,
    _merge_multiline,
    _reconstruction_reliable,
    _section_header_reliable,
    _segment_into_sections,
)

# Column x-centers used across the fixtures (POS, BENENNUNG, STK, WERKSTOFF).
_X_POS = 30.0
_X_NAME = 120.0
_X_QTY = 260.0
_X_MAT = 340.0


def _word(page: int, x_center: float, y: float, text: str, width: float = 40.0) -> Word:
    return Word(
        page=page,
        x0=x_center - width / 2,
        y0=y,
        x1=x_center + width / 2,
        y1=y + 8.0,
        text=text,
    )


def _header_row(y: float) -> list[Word]:
    return [
        _word(0, _X_POS, y, "POS"),
        _word(0, _X_NAME, y, "BENENNUNG"),
        _word(0, _X_QTY, y, "STK"),
        _word(0, _X_MAT, y, "WERKSTOFF"),
    ]


def _reconstruct(words: list[Word]):
    bands = _cluster_into_bands(words)
    sections = _segment_into_sections(bands)
    rows, keys, locations = _assign_cells(sections)
    rows, keys, locations = _merge_multiline(rows, keys, locations, sections)
    return bands, sections, rows, keys


def test_t007_five_parts_same_position_yield_five_rows() -> None:
    """5 distinct parts, all carrying position '10', on 5 distinct y-bands."""
    words = _header_row(10.0)
    names = ["EINSATZ_A", "EINSATZ_B", "KERN_C", "SCHIEBER_D", "PLATTE_E"]
    for i, name in enumerate(names):
        y = 30.0 + i * 20.0
        words += [
            _word(0, _X_POS, y, "10"),
            _word(0, _X_NAME, y, name),
            _word(0, _X_QTY, y, str(i + 1)),
            _word(0, _X_MAT, y, "1.2343"),
        ]

    _, _, rows, keys = _reconstruct(words)

    assert len(rows) == 5, f"expected 5 rows, got {len(rows)}"
    assert len(set(keys)) == 5, "row band ids must be distinct (identity != position)"
    # The position value is identical across all five — proving identity is the band.
    pos_col = next(k for k in rows[0] if k.upper().startswith("POS"))
    assert {r[pos_col] for r in rows} == {"10"}
    name_col = next(k for k in rows[0] if "BENENN" in k.upper())
    assert {r[name_col] for r in rows} == set(names)


def test_nameless_rows_preserved_as_distinct() -> None:
    """3 rows with NO position number but distinct content stay distinct."""
    words = _header_row(10.0)
    names = ["DICHTRING", "FEDER", "STIFT"]
    for i, name in enumerate(names):
        y = 30.0 + i * 20.0
        words += [
            # no POS token in the position corridor
            _word(0, _X_NAME, y, name),
            _word(0, _X_QTY, y, str(i + 2)),
            _word(0, _X_MAT, y, "AA-1000"),
        ]

    _, _, rows, keys = _reconstruct(words)

    assert len(rows) == 3, f"nameless rows collapsed: got {len(rows)}"
    assert len(set(keys)) == 3
    name_col = next(k for k in rows[0] if "BENENN" in k.upper())
    assert {r[name_col] for r in rows} == set(names)
    # Each nameless row keeps its qty/material — NOT merged as a description overflow.
    mat_col = next(k for k in rows[0] if "WERKST" in k.upper())
    assert all(r[mat_col] == "AA-1000" for r in rows)


def test_pdf_row_bands_count_independent_of_position() -> None:
    """The completeness anchor counts bands, so 5 same-position parts = 5 bands."""
    words = _header_row(10.0)
    for i in range(5):
        y = 30.0 + i * 20.0
        words += [
            _word(0, _X_POS, y, "10"),
            _word(0, _X_NAME, y, f"PART_{i}"),
            _word(0, _X_QTY, y, "1"),
            _word(0, _X_MAT, y, "1.2343"),
        ]

    _, sections, _, _ = _reconstruct(words)
    data_bands = [
        b.band_id
        for s in sections
        for b in s.data_bands
        if not b.is_header and not b.is_continuation
    ]
    assert len(data_bands) == 5
    assert len(set(data_bands)) == 5


def test_stacked_multiline_header_uses_finest_grid() -> None:
    """A 3-line stacked header folds into one section; the finest line drives cols."""
    words: list[Word] = []
    # Header line 1 (coarse, spanning): two keyword tokens, no values.
    words += [_word(0, _X_POS, 8.0, "Nr."), _word(0, _X_MAT, 8.0, "Material")]
    # Header line 2 (coarse): Pos./Anzahl.
    words += [_word(0, _X_POS, 18.0, "Pos."), _word(0, _X_QTY, 18.0, "Anzahl")]
    # Header line 3 (finest — most tokens): drives the corridors.
    words += _header_row(28.0)
    # Two data rows below.
    for i, name in enumerate(["TEIL_A", "TEIL_B"]):
        y = 50.0 + i * 20.0
        words += [
            _word(0, _X_POS, y, str(i + 1)),
            _word(0, _X_NAME, y, name),
            _word(0, _X_QTY, y, "2"),
            _word(0, _X_MAT, y, "1.2343"),
        ]

    bands = _cluster_into_bands(words)
    sections = _segment_into_sections(bands)
    assert len(sections) == 1, "stacked header must yield ONE section"
    # Finest line (4 cols) wins over the 2-token coarse lines.
    assert len(sections[0].corridors) == 4
    assert any("BENENN" in c.name.upper() for c in sections[0].corridors)
    rows, keys, _ = _assign_cells(sections)
    assert len(rows) == 2 and len(set(keys)) == 2


def test_banner_block_above_header_is_rejected() -> None:
    """A weak title/banner block must not win over the real (stronger) header.

    Mirrors the audi/TCG/Mercedes failure: a company/project banner with a couple
    of incidental keyword tokens sat above the genuine BOM header. The strongest
    candidate on the page must drive the columns; the banner is dropped.
    """
    words: list[Word] = []
    # Banner near the top: only 2 keyword-ish tokens, isolated.
    words += [_word(0, _X_POS, 8.0, "Projekt"), _word(0, _X_MAT, 8.0, "Material:")]
    # An unrelated metadata line between banner and the real header.
    words += [_word(0, _X_POS, 20.0, "Datum"), _word(0, _X_NAME, 20.0, "13.07.2022")]
    # The real header (4 column keywords) lower down.
    words += _header_row(40.0)
    for i, name in enumerate(["TEIL_A", "TEIL_B"]):
        y = 60.0 + i * 20.0
        words += [
            _word(0, _X_POS, y, str(i + 1)),
            _word(0, _X_NAME, y, name),
            _word(0, _X_QTY, y, "2"),
            _word(0, _X_MAT, y, "1.2343"),
        ]

    sections = _segment_into_sections(_cluster_into_bands(words))
    assert len(sections) == 1
    names = [c.name.upper() for c in sections[0].corridors]
    assert any("BENENN" in n for n in names), f"banner won instead of header: {names}"
    assert "PROJEKT" not in names
    rows, _, _ = _assign_cells(sections)
    assert len(rows) == 2  # banner + metadata dropped, only the 2 data rows remain


def test_page_marker_footer_is_dropped_not_a_row() -> None:
    """A '… Seite N …' footer is removed by content; real rows are untouched."""
    words = _header_row(10.0)
    for i, name in enumerate(["TEIL_A", "TEIL_B"]):
        y = 30.0 + i * 20.0
        words += [
            _word(0, _X_POS, y, str(i + 1)),
            _word(0, _X_NAME, y, name),
            _word(0, _X_QTY, y, "2"),
            _word(0, _X_MAT, y, "1.2343"),
        ]
    # Page footer at the bottom: date + "Seite 2" + filename.
    words += [
        _word(0, _X_POS, 80.0, "26.11.2025"),
        _word(0, _X_NAME, 80.0, "Seite"),
        _word(0, _X_QTY, 80.0, "2"),
        _word(0, _X_MAT, 80.0, "Stueckliste.xlsx"),
    ]

    sections = _segment_into_sections(_cluster_into_bands(words))
    rows, keys, locs = _assign_cells(sections)
    rows, _, _ = _merge_multiline(rows, keys, locs, sections)

    assert len(rows) == 2  # footer dropped, two real rows kept
    name_col = next(k for k in rows[0] if "BENENN" in k.upper())
    assert {r[name_col] for r in rows} == {"TEIL_A", "TEIL_B"}
    assert all("Seite" not in (r[name_col] or "") for r in rows)


def test_source_locations_carry_page_and_bbox() -> None:
    """Each assigned cell gets a page+bbox+text location (Check-2 GREEN evidence)."""
    words = _header_row(10.0)
    words += [
        _word(0, _X_POS, 30.0, "1"),
        _word(0, _X_NAME, 30.0, "FORMPLATTE"),
        _word(0, _X_QTY, 30.0, "2"),
        _word(0, _X_MAT, 30.0, "1.2343"),
    ]
    sections = _segment_into_sections(_cluster_into_bands(words))
    _, _, locations = _assign_cells(sections)

    assert len(locations) == 1
    name_col = next(k for k in locations[0] if "BENENN" in k.upper())
    loc = locations[0][name_col]
    assert loc["page"] == 1
    assert loc["text"] == "FORMPLATTE"
    assert loc["match_type"] == "column_corridor"
    assert len(loc["bbox"]) == 4


def test_boundary_value_is_flagged_centered_value_is_not() -> None:
    """A value between two column anchors → column_boundary (capped YELLOW); a
    value centred in its column → column_corridor (eligible for GREEN)."""
    words = _header_row(10.0)
    # Row 1: all values centred on their anchors → confident.
    words += [
        _word(0, _X_POS, 30.0, "1"),
        _word(0, _X_NAME, 30.0, "FORMPLATTE"),
        _word(0, _X_QTY, 30.0, "2"),
        _word(0, _X_MAT, 30.0, "1.2343"),
    ]
    # Row 2: a single token sitting at the BENENNUNG/STK midpoint (ambiguous).
    midpoint = (_X_NAME + _X_QTY) / 2  # 190
    words += [
        _word(0, _X_POS, 50.0, "2"),
        _word(0, midpoint, 50.0, "AMBIG", width=10.0),
        _word(0, _X_MAT, 50.0, "1.2343"),
    ]

    sections = _segment_into_sections(_cluster_into_bands(words))
    _, _, locations = _assign_cells(sections)

    # Row 1: the description cell is confidently in its corridor.
    name_col = next(k for k in locations[0] if "BENENN" in k.upper())
    assert locations[0][name_col]["match_type"] == "column_corridor"
    # Row 2: the ambiguous token's cell is flagged as a boundary.
    boundary = [
        c["match_type"] for loc in locations for c in loc.values()
        if c["text"] == "AMBIG"
    ]
    assert boundary == ["column_boundary"]


def test_degenerate_header_section_is_unreliable() -> None:
    """A section whose header collapsed to repeated tokens (GF) is not reliable."""
    degenerate = [
        ColumnCorridor(name=n, x_left=0, x_right=1, center=0)
        for n in ["HRC", "HRC_2", "HRC_3", "HRC_4", "HRC_5", "HRC_6"]
    ]
    healthy = [
        ColumnCorridor(name=n, x_left=0, x_right=1, center=0)
        for n in ["Pos.", "Anzahl", "Modellname", "Bezeichnung", "Fertigmaße"]
    ]
    assert _section_header_reliable(degenerate) is False
    assert _section_header_reliable(healthy) is True


def test_reconstruction_declines_when_most_rows_are_degenerate() -> None:
    """GF-like: every section degenerate → reconstruction must be declined."""

    def _band(bid: str) -> RowBand:
        return RowBand(band_id=bid, page=0, words=[], y0=0.0, y1=1.0)

    degenerate = TableSection(
        corridors=[
            ColumnCorridor(name=n, x_left=0, x_right=1, center=0)
            for n in ["HRC", "HRC_2", "HRC_3"]
        ],
        data_bands=[_band(f"p0:b{i:04d}") for i in range(10)],
    )
    healthy = TableSection(
        corridors=[
            ColumnCorridor(name=n, x_left=0, x_right=1, center=0)
            for n in ["Pos.", "Benennung", "Material", "Menge"]
        ],
        data_bands=[_band(f"p1:b{i:04d}") for i in range(10)],
    )
    assert _reconstruction_reliable([degenerate]) is False
    assert _reconstruction_reliable([healthy]) is True
    # Mixed but majority healthy → still accepted.
    assert _reconstruction_reliable([degenerate, healthy, healthy]) is True


def test_value_dominated_row_is_not_a_header() -> None:
    """A data row full of codes/dimensions must not be mistaken for a header."""
    band = RowBand(
        band_id="p0:b0099",
        page=0,
        words=[
            _word(0, _X_POS, 100.0, "1-32"),
            _word(0, _X_NAME, 100.0, "1_5670_1_21_01-032_VERTEILERBLO"),
            _word(0, 200.0, 100.0, "Verteilerblock"),
            _word(0, 300.0, 100.0, "400x78x55"),
            _word(0, 360.0, 100.0, "S355J2G3"),
            _word(0, 420.0, 100.0, "1.0570"),
            _word(0, 480.0, 100.0, "12.198"),
        ],
        y0=100.0,
        y1=108.0,
    )
    assert _is_header_band(band) is False


def test_real_header_line_is_a_header() -> None:
    """The genuine granular header line must still be detected."""
    band = _cluster_into_bands(_header_row(8.0))[0]
    assert _is_header_band(band) is True


def test_description_overflow_is_merged_not_counted_as_row() -> None:
    """A pure description-continuation band folds into its anchor (D1 rule)."""
    words = _header_row(10.0)
    # Anchor row at y=30 with full cells.
    words += [
        _word(0, _X_POS, 30.0, "1"),
        _word(0, _X_NAME, 30.0, "FORMPLATTE"),
        _word(0, _X_QTY, 30.0, "1"),
        _word(0, _X_MAT, 30.0, "1.2343"),
    ]
    # Continuation band at y=42: ONLY a description-column token, nothing else.
    words += [_word(0, _X_NAME, 42.0, "OBERTEIL")]
    # Next real row at y=64.
    words += [
        _word(0, _X_POS, 64.0, "2"),
        _word(0, _X_NAME, 64.0, "KERN"),
        _word(0, _X_QTY, 64.0, "4"),
        _word(0, _X_MAT, 64.0, "1.2344"),
    ]

    _, _, rows, keys = _reconstruct(words)

    assert len(rows) == 2, f"continuation not merged: got {len(rows)} rows"
    name_col = next(k for k in rows[0] if "BENENN" in k.upper())
    assert rows[0][name_col] == "FORMPLATTE OBERTEIL"
    assert rows[1][name_col] == "KERN"
