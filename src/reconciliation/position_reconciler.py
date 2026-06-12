"""Position Reconciler — guarantees no PDF position is lost before scoring.

Runs AFTER transform_bom, BEFORE score_bom_async. Builds the master set

    master_set = set(extracted positions) ∪ set(raw PDF positions)

and synthesises a MISSING row for every position present in the PDF but absent
from the extracted rows — for BOTH the text-layer and the Vision path. The
synthetic cells carry method ``synthetic_pdf_only_missing`` so the scorer can
hard-veto them to RED, and the row is flagged ``is_synthetic=True``.

Hard limit (documented): on the Vision path the PDF-side set is the position
column of the raw extracted rows. Positions the Vision model never read are not
in that set and therefore cannot be recovered — there is no ground truth for
them without a text layer.
"""

from __future__ import annotations

import logging

from src.core.models import (
    CellTransformation,
    TransformationResult,
    TransformedRow,
)
from src.core.positions import POSITION_FIELDS as _POSITION_FIELDS
from src.core.positions import normalize_position as _normalize
from src.mapping.schema_registry import TargetSchema

logger = logging.getLogger(__name__)

_MISSING_METHOD = "synthetic_pdf_only_missing"
_MISSING_NOTE = "Position im PDF gefunden, aber nicht extrahiert — MISSING"


def _row_position(row: TransformedRow) -> str:
    """Return the normalized position of an extracted row (or '')."""
    for field in _POSITION_FIELDS:
        cell = row.get_cell(field)
        if cell is not None:
            normalized = _normalize(cell.transformed_value)
            if normalized:
                return normalized
    return ""


def _position_field_name(schema: TargetSchema) -> str:
    for name in _POSITION_FIELDS:
        if schema.field_by_name.get(name):
            return name
    return _POSITION_FIELDS[0]


def reconcile_positions(
    transform_result: TransformationResult,
    raw_pdf_positions: list[str],
    schema: TargetSchema,
    pdf_row_bands: list[str] | None = None,
    raw_pdf_position_counts: dict[str, int] | None = None,
) -> TransformationResult:
    """Append synthetic MISSING rows for PDF-only positions; set the master set.

    Two modes:

    * RB-1 ROW-BAND mode (``pdf_row_bands`` given — deterministic text-layer path):
      the master set is the set of spatial row-band ids. Row identity is the band,
      not the position value, so N parts sharing a position (T-007) and nameless
      rows are each counted. ``expected_row_keys`` becomes the guard basis.
    * POSITION mode (default — Vision/scan path, no row bands): the master set is
      ``set(extracted positions) ∪ set(raw_pdf_positions)``.  Additionally, if
      ``raw_pdf_position_counts`` is provided, positions that appeared more than
      once in the RAW rows but were extracted fewer times get extra synthetic
      MISSING rows so under-extraction is visible (BUG-011).

    Mutates and returns ``transform_result``.
    """
    if pdf_row_bands:
        return _reconcile_by_row_bands(transform_result, pdf_row_bands, schema)

    extracted: set[str] = set()
    for row in transform_result.rows:
        position = _row_position(row)
        if position:
            extracted.add(position)

    pdf_positions = {
        normalized
        for raw in (raw_pdf_positions or [])
        if (normalized := _normalize(raw))
    }

    master_set = extracted | pdf_positions
    missing = sorted(pdf_positions - extracted)

    pos_field = _position_field_name(schema)
    field_def = schema.field_by_name.get(pos_field)
    pos_col = field_def.column if field_def is not None else "A"

    next_index = max((row.row_index for row in transform_result.rows), default=-1) + 1

    synthetic_rows: list[TransformedRow] = []
    for offset, position in enumerate(missing):
        synthetic_rows.append(
            TransformedRow(
                row_index=next_index + offset,
                is_synthetic=True,
                cells=[
                    CellTransformation(
                        target_field=pos_field,
                        target_column=pos_col,
                        source_column="",
                        raw_value=None,
                        transformed_value=position,
                        confidence=0.0,
                        method=_MISSING_METHOD,
                        notes=_MISSING_NOTE,
                    )
                ],
            )
        )

    next_index += len(synthetic_rows)

    # BUG-011: Unterdeckungs-Synthese für doppelte Positionsnummern.
    # Wenn eine Position p in den RAW-Zeilen pdf_count Mal vorkam, aber nach
    # Deduplizierung/Postvalidierung nur extracted_count Nicht-synthetische Zeilen
    # mit dieser Position existieren, werden (pdf_count - extracted_count) zusätzliche
    # synthetische MISSING-Zeilen erzeugt — sichtbare RED statt stiller Verlust.
    # Positionen, die KOMPLETT fehlen, werden bereits vom missing-Pfad oben abgedeckt.
    if raw_pdf_position_counts:
        # Build per-position count of NON-synthetic extracted rows
        extracted_count_by_pos: dict[str, int] = {}
        for row in transform_result.rows:
            if row.is_synthetic:
                continue
            pos = _row_position(row)
            if pos:
                extracted_count_by_pos[pos] = extracted_count_by_pos.get(pos, 0) + 1

        underextraction_synthetic: list[TransformedRow] = []
        for raw_pos, pdf_count in raw_pdf_position_counts.items():
            norm_pos = _normalize(raw_pos)
            if not norm_pos:
                continue
            if pdf_count < 2:
                continue
            # Only handle positions that are NOT completely missing (those are
            # handled by the missing-set path above).
            if norm_pos not in extracted:
                continue
            extracted_c = extracted_count_by_pos.get(norm_pos, 0)
            shortfall = pdf_count - extracted_c
            if shortfall <= 0:
                continue
            logger.warning(
                "Reconciler (BUG-011): position %r appeared %d time(s) in RAW rows "
                "but only %d non-synthetic extracted row(s) found — injecting %d "
                "additional synthetic MISSING row(s)",
                norm_pos,
                pdf_count,
                extracted_c,
                shortfall,
            )
            for i in range(shortfall):
                underextraction_synthetic.append(
                    TransformedRow(
                        row_index=next_index,
                        is_synthetic=True,
                        cells=[
                            CellTransformation(
                                target_field=pos_field,
                                target_column=pos_col,
                                source_column="",
                                raw_value=None,
                                transformed_value=norm_pos,
                                confidence=0.0,
                                method=_MISSING_METHOD,
                                notes=(
                                    f"{_MISSING_NOTE} "
                                    f"(Position {norm_pos}: {extracted_c} von "
                                    f"{pdf_count} Vorkommen extrahiert)"
                                ),
                            )
                        ],
                    )
                )
                next_index += 1
        synthetic_rows.extend(underextraction_synthetic)

    transform_result.rows = [*transform_result.rows, *synthetic_rows]
    transform_result.reconciled = True

    if master_set:
        transform_result.expected_position_ids = sorted(master_set)
        transform_result.expected_position_count = len(master_set)
        transform_result.guard_basis = "position_set"
    else:
        # ZDL-2: no position anchor at all (no position column recognised and no
        # PDF-side positions). Do NOT silently disable the zero-data-loss guard:
        # fall back to the distinct extracted-row count as a lower bound so a row
        # dropped later in the pipeline still trips the export assertion.
        non_synthetic = sum(
            1 for row in transform_result.rows if not row.is_synthetic
        )
        transform_result.expected_position_ids = []
        transform_result.expected_position_count = non_synthetic
        transform_result.guard_basis = "row_count_fallback" if non_synthetic else "none"
        logger.warning(
            "Reconciler: no position anchor — guard falls back to row count "
            "(%d rows). Vollständigkeit nicht über Positions-IDs garantiert.",
            non_synthetic,
        )

    if missing:
        logger.warning(
            "Reconciler: %d PDF-only position(s) re-injected as MISSING "
            "(extracted=%d, pdf=%d, master_set=%d)",
            len(missing),
            len(extracted),
            len(pdf_positions),
            len(master_set),
        )
    else:
        logger.info(
            "Reconciler: no PDF-only positions (extracted=%d, pdf=%d, master_set=%d)",
            len(extracted),
            len(pdf_positions),
            len(master_set),
        )

    return transform_result


def _reconcile_by_row_bands(
    transform_result: TransformationResult,
    pdf_row_bands: list[str],
    schema: TargetSchema,
) -> TransformationResult:
    """RB-1 master set on deterministic spatial row-band ids.

    ``master = {row.source_row_id} ∪ set(pdf_row_bands)``. A band the deterministic
    parser found but that is absent from the transformed rows (a downstream drop)
    is re-injected as a synthetic MISSING row keyed by that band id, so it surfaces
    as RED instead of vanishing. On the clean deterministic path extraction equals
    the anchor, so ``missing`` is normally empty — this is the defensive net.
    """
    extracted = {row.source_row_id for row in transform_result.rows if row.source_row_id}
    bands = {b for b in pdf_row_bands if b}
    master_set = extracted | bands
    missing = sorted(bands - extracted)

    pos_field = _position_field_name(schema)
    field_def = schema.field_by_name.get(pos_field)
    pos_col = field_def.column if field_def is not None else "A"
    next_index = max((row.row_index for row in transform_result.rows), default=-1) + 1

    synthetic_rows: list[TransformedRow] = []
    for offset, band_id in enumerate(missing):
        synthetic_rows.append(
            TransformedRow(
                row_index=next_index + offset,
                source_row_id=band_id,
                is_synthetic=True,
                cells=[
                    CellTransformation(
                        target_field=pos_field,
                        target_column=pos_col,
                        source_column="",
                        raw_value=None,
                        transformed_value=None,
                        confidence=0.0,
                        method=_MISSING_METHOD,
                        notes=f"{_MISSING_NOTE} (Band {band_id})",
                    )
                ],
            )
        )

    transform_result.rows = [*transform_result.rows, *synthetic_rows]
    transform_result.reconciled = True
    transform_result.expected_row_keys = sorted(master_set)
    transform_result.expected_position_count = len(master_set)
    transform_result.guard_basis = "row_band_set"

    if missing:
        logger.warning(
            "Reconciler (row-band): %d band(s) re-injected as MISSING "
            "(extracted=%d, bands=%d, master_set=%d)",
            len(missing),
            len(extracted),
            len(bands),
            len(master_set),
        )
    else:
        logger.info(
            "Reconciler (row-band): no dropped bands "
            "(extracted=%d, bands=%d, master_set=%d)",
            len(extracted),
            len(bands),
            len(master_set),
        )

    return transform_result
