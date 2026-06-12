"""Transform Pipeline — orchestrates value transformation for a mapped BOM.

Takes ParsedBOM + MappingResult → applies per-field transformations → produces
TransformationResult ready for confidence scoring and export.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.core.models import (
    CellTransformation,
    ParsedBOM,
    SourceLocation,
    TransformationResult,
    TransformedRow,
)
from src.mapping.llm_column_mapper import MappingResult
from src.mapping.schema_registry import TargetSchema, load_schema
from src.transform.master_data_matcher import (
    get_coating_catalog,
    get_manufacturer_catalog,
    get_material_catalog,
    get_nitriding_catalog,
    get_parts_group_catalog,
)
from src.transform.value_transformer import (
    clean_text,
    coerce_decimal,
    coerce_integer,
    convert_inch_to_mm,
    extract_hardness_from_material,
    is_pure_combined_dimension,
    normalize_boolean,
    parse_dimensions,
    parse_hardness,
    parse_nitriding_depth,
)

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# Fields that should contain a single dimension component from a combined string
_DIMENSION_FIELDS = {"Dimensions X/D", "Dimensions Y/L", "Dimensions Z"}


def transform_bom(
    bom: ParsedBOM,
    mapping: MappingResult,
    schema: TargetSchema | None = None,
) -> TransformationResult:
    """Transform all rows of a parsed BOM using the column mapping.

    For each row:
    1. Look up the source column for each target field via the mapping
    2. Apply field-specific transformation logic
    3. Run master-data matching where applicable
    """
    if schema is None:
        schema = load_schema()

    result = TransformationResult(
        source_file=bom.source.filename,
        customer=bom.source.customer,
        # AW-5: parser's pre-reconcile count, carried only as a diagnostic. The
        # position reconciler overwrites expected_position_count / _ids with the
        # master set, and the scorer stamps the audit from there — this value is
        # NOT the guard threshold on its own.
        expected_position_count=bom.expected_position_count,
    )

    # Build target→source lookup from mapping
    target_to_source: dict[str, str] = {}
    for m in mapping.mappings:
        if m.target_field and m.source_column:
            target_to_source[m.target_field] = m.source_column

    # Detect if any dimension field maps to a combined dimension source
    # (i.e. all 3 dimension fields map to the same source column)
    dim_sources = {
        f: target_to_source.get(f) for f in _DIMENSION_FIELDS if target_to_source.get(f)
    }
    combined_dim_source = None
    dim_source_vals = [v for v in dim_sources.values() if v]
    if dim_source_vals:
        # If all dimension fields point to the same source, it's a combined field
        if len(set(dim_source_vals)) == 1:
            combined_dim_source = dim_source_vals[0]
        # Also if only X/D is mapped, try to split it
        elif len(dim_source_vals) == 1 and "Dimensions X/D" in dim_sources:
            combined_dim_source = dim_sources["Dimensions X/D"]

    # Value-driven fallback: the topology check above only fires when the mapper
    # routed a combined "AxBxC" column onto a dimension field. When the mapper
    # missed it (left it unmapped, or — for a foreign header like "尺寸 (mm)" —
    # mislabelled it), the column's VALUES still betray it. Recover the split
    # here. This never drops a row and never grants GREEN (the split components
    # still pass through scoring + source value-match), so the zero-data-loss and
    # zero-false-green guarantees are untouched.
    if combined_dim_source is None:
        combined_dim_source = _detect_combined_dim_source_by_value(
            bom, target_to_source
        )

    # Load master-data catalogs (lazy singletons)
    mat_catalog = get_material_catalog()
    nit_catalog = get_nitriding_catalog()
    coat_catalog = get_coating_catalog()
    pg_catalog = get_parts_group_catalog()
    man_catalog = get_manufacturer_catalog()

    stats = {
        "total_cells": 0,
        "transformed": 0,
        "passthrough": 0,
        "empty": 0,
        "master_data_matched": 0,
        "dimension_split": 0,
        "hardness_parsed": 0,
        "inch_converted": 0,
    }
    transformed_rows: list[TransformedRow] = []

    for row_idx, row_data in enumerate(bom.rows):
        row_cells: list[CellTransformation] = []

        # Process dimension splitting first (affects 3 target fields)
        dim_parsed = None
        if combined_dim_source and combined_dim_source in row_data:
            raw_dim = row_data.get(combined_dim_source) or ""
            if raw_dim.strip():
                dim_parsed = parse_dimensions(raw_dim)
                if dim_parsed.get("is_inch"):
                    stats["inch_converted"] += 1

        # Also check if material field contains embedded hardness
        mat_source = target_to_source.get("Material")
        embedded_hardness = None
        if mat_source:
            raw_mat = row_data.get(mat_source) or ""
            embedded_hardness = extract_hardness_from_material(raw_mat)

        for field in schema.fields:
            stats["total_cells"] += 1
            source_col = target_to_source.get(field.name)

            # --- Dimension fields: special handling ---
            if field.name in _DIMENSION_FIELDS and dim_parsed:
                comp = {
                    "Dimensions X/D": "x",
                    "Dimensions Y/L": "y",
                    "Dimensions Z": "z",
                }
                dim_key = comp.get(field.name, "x")
                dim_val = dim_parsed.get(dim_key)
                if dim_val:
                    if dim_parsed.get("is_inch"):
                        dim_val = convert_inch_to_mm(dim_val)
                    stats["dimension_split"] += 1
                    stats["transformed"] += 1
                    row_cells.append(
                        CellTransformation(
                            target_field=field.name,
                            target_column=field.column,
                            source_column=combined_dim_source or "",
                            raw_value=row_data.get(combined_dim_source or ""),
                            transformed_value=dim_val,
                            confidence=0.9 if dim_parsed.get("is_inch") else 0.95,
                            method="dimension_split",
                            notes="Split from combined dimension string"
                            + (
                                " + inch→mm conversion"
                                if dim_parsed.get("is_inch")
                                else ""
                            ),
                        )
                    )
                    continue
                # Dimension field but no value for this component
                if not source_col:
                    stats["empty"] += 1
                    row_cells.append(
                        CellTransformation(
                            target_field=field.name,
                            target_column=field.column,
                            confidence=0.0,
                            method="empty",
                        )
                    )
                    continue

            # --- No source mapping for this field ---
            if not source_col:
                stats["empty"] += 1
                row_cells.append(
                    CellTransformation(
                        target_field=field.name,
                        target_column=field.column,
                        confidence=0.0,
                        method="empty",
                    )
                )
                continue

            raw_value = row_data.get(source_col)
            if not raw_value or not str(raw_value).strip():
                stats["empty"] += 1
                row_cells.append(
                    CellTransformation(
                        target_field=field.name,
                        target_column=field.column,
                        source_column=source_col,
                        raw_value=raw_value,
                        confidence=0.0,
                        method="empty",
                    )
                )
                continue

            raw_str = str(raw_value).strip()

            # --- Apply field-specific transformations ---
            cell = _transform_field(
                field_name=field.name,
                field_type=field.type,
                field_column=field.column,
                source_col=source_col,
                raw_str=raw_str,
                mat_catalog=mat_catalog,
                nit_catalog=nit_catalog,
                coat_catalog=coat_catalog,
                pg_catalog=pg_catalog,
                man_catalog=man_catalog,
                embedded_hardness=embedded_hardness,
                stats=stats,
            )
            row_cells.append(cell)

        # RB-1: carry the deterministic band id (text-layer coordinate path) as the
        # stable row identity. Empty on the Vision/scan path → the reconciler then
        # falls back to the position-set guard unchanged.
        source_row_id = bom.row_keys[row_idx] if row_idx < len(bom.row_keys) else ""

        transformed_rows = transformed_rows + [
            TransformedRow(
                row_index=row_idx, source_row_id=source_row_id, cells=row_cells
            )
        ]

    result.rows = transformed_rows

    # Propagate validation flags from parsed BOM metadata (from Vision post-validation)
    flags_dict = bom.metadata.get("row_validation_flags")
    if isinstance(flags_dict, dict):
        for row_idx_str, flags in flags_dict.items():
            row_idx = int(row_idx_str) if isinstance(row_idx_str, str) else row_idx_str
            if isinstance(flags, list):
                result.row_validation_flags[row_idx] = flags

    # Propagate lossless non-data (footer/header/note) flags — advisory only.
    non_data_flags = bom.metadata.get("non_data_row_flags")
    if isinstance(non_data_flags, dict):
        for row_idx_raw, reasons in non_data_flags.items():
            row_idx = int(row_idx_raw) if isinstance(row_idx_raw, str) else row_idx_raw
            if isinstance(row_idx, int) and isinstance(reasons, list):
                result.non_data_row_flags[row_idx] = reasons

    # Propagate PDF source and text-layer info for deterministic verification
    from src.core.models import FileFormat

    result.source_is_pdf = bom.source.format == FileFormat.PDF
    result.extraction_method = bom.source.extraction_method
    result.source_extraction_confidence = float(bom.source.extraction_confidence or 0.0)
    result.has_text_layer = bool(bom.metadata.get("has_text_layer", False))
    fallback_reason = bom.metadata.get("vision_fallback_reason")
    if isinstance(fallback_reason, str):
        fallback_reason = fallback_reason.strip()
    else:
        fallback_reason = ""
    result.vision_fallback_reason = fallback_reason or None

    extraction_notes = bom.metadata.get("notes")
    if isinstance(extraction_notes, str):
        result.extraction_notes = extraction_notes.strip()

    check2_reason = bom.metadata.get("check2_reason")
    if isinstance(check2_reason, str):
        result.check2_reason = check2_reason.strip()
    elif result.extraction_method and result.has_text_layer:
        result.check2_reason = "text_layer_direct"

    result.extraction_json_repaired = bool(bom.metadata.get("llm_json_repaired", False))

    document_text_layer = bom.metadata.get("document_text_layer")
    if isinstance(document_text_layer, str):
        result.document_text_layer = document_text_layer.strip()

    document_text_pages = bom.metadata.get("document_text_pages")
    if isinstance(document_text_pages, list):
        result.document_text_pages = [
            str(page_text).strip()
            for page_text in document_text_pages
            if isinstance(page_text, str) and str(page_text).strip()
        ]

    uncertain_cells = bom.metadata.get("llm_uncertain_cells")
    if isinstance(uncertain_cells, dict):
        for row_idx_raw, columns in uncertain_cells.items():
            row_idx = int(row_idx_raw) if isinstance(row_idx_raw, str) else row_idx_raw
            if not isinstance(row_idx, int) or not isinstance(columns, list):
                continue
            normalized_columns = [
                str(column).strip()
                for column in columns
                if isinstance(column, str) and str(column).strip()
            ]
            if normalized_columns:
                result.extraction_uncertain_cells[row_idx] = normalized_columns

    source_locations = bom.metadata.get("source_locations")
    if isinstance(source_locations, dict):
        for row_idx_raw, row_locations in source_locations.items():
            row_idx = int(row_idx_raw) if isinstance(row_idx_raw, str) else row_idx_raw
            if not isinstance(row_idx, int) or not isinstance(row_locations, dict):
                continue

            normalized_row_locations: dict[str, SourceLocation] = {}
            for source_column, location in row_locations.items():
                if not isinstance(source_column, str) or not isinstance(location, dict):
                    continue
                normalized_row_locations[source_column] = SourceLocation(
                    page=location.get("page"),
                    bbox=location.get("bbox"),
                    text=str(location.get("text") or ""),
                    match_type=str(location.get("match_type") or ""),
                )

            if normalized_row_locations:
                result.source_locations[row_idx] = normalized_row_locations

    result.stats = stats
    logger.info(
        "Transformed %s: %d rows, %d cells total | "
        "%d transformed, %d passthrough, %d empty | "
        "%d dimension splits, %d hardness parsed, %d master-data matched",
        bom.source.filename,
        len(result.rows),
        stats["total_cells"],
        stats["transformed"],
        stats["passthrough"],
        stats["empty"],
        stats["dimension_split"],
        stats["hardness_parsed"],
        stats["master_data_matched"],
    )
    return result


def _detect_combined_dim_source_by_value(
    bom: ParsedBOM, target_to_source: dict[str, str]
) -> str | None:
    """Find a combined-dimension source column from its VALUES, not the mapping.

    Only ever consulted when topology-based detection found nothing. Returns the
    source column whose cells are predominantly whole-cell "AxB"/"AxBxC" values.

    Safety: an unmapped column (or one already mapped to a dimension field) is
    accepted on a low bar; a column mapped to a NON-dimension field is only
    hijacked when its values are *overwhelmingly* dimensional — a regime that
    text/notes/part-numbers do not reach — so we never steal a legitimately
    non-dimensional column.
    """
    mapped_sources = {v for v in target_to_source.values() if v}
    dim_mapped = {target_to_source.get(f) for f in _DIMENSION_FIELDS}
    fallback: str | None = None

    for col in bom.headers:
        nonempty = [
            str(row.get(col) or "").strip()
            for row in bom.rows
            if str(row.get(col) or "").strip()
        ]
        if len(nonempty) < 2:
            continue
        pure = sum(1 for v in nonempty if is_pure_combined_dimension(v))
        if pure < 2:
            continue
        frac = pure / len(nonempty)
        unmapped_or_dim = col not in mapped_sources or col in dim_mapped
        if unmapped_or_dim and frac >= 0.5:
            return col
        if not unmapped_or_dim and pure >= 3 and frac >= 0.7:
            fallback = fallback or col
    return fallback


def _transform_field(
    *,
    field_name: str,
    field_type: str,
    field_column: str,
    source_col: str,
    raw_str: str,
    mat_catalog,
    nit_catalog,
    coat_catalog,
    pg_catalog,
    man_catalog,
    embedded_hardness: str | None,
    stats: dict,
) -> CellTransformation:
    """Transform a single field value based on field name and type."""

    # --- Material ---
    if field_name == "Material":
        match = mat_catalog.match(raw_str)
        if match.canonical:
            stats["master_data_matched"] += 1
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=match.canonical,
                confidence=match.confidence,
                method=f"master_data:{match.method}",
                notes=f"Matched to canonical material '{match.canonical}'",
            )
        # No match — pass through cleaned
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.5,
            method="passthrough",
            notes="No master-data match; raw value passed through",
        )

    # --- Hardness ---
    if field_name == "Hardness":
        parsed = parse_hardness(raw_str)
        if parsed["value"] and parsed["value"] != raw_str:
            stats["hardness_parsed"] += 1
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=parsed["value"],
                confidence=0.9,
                method="regex_parse",
                notes=f"Parsed hardness: unit={parsed['unit']}",
            )
        # Already in canonical hardness format (recognized but value unchanged)
        if parsed["value"] and parsed["unit"]:
            stats["hardness_parsed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=parsed["value"],
                confidence=0.9,
                method="regex_parse",
                notes=f"Already canonical hardness format: unit={parsed['unit']}",
            )
        # Check if we have embedded hardness from material field
        if embedded_hardness:
            eparsed = parse_hardness(embedded_hardness)
            if eparsed["value"]:
                stats["hardness_parsed"] += 1
                stats["transformed"] += 1
                return CellTransformation(
                    target_field=field_name,
                    target_column=field_column,
                    source_column=source_col,
                    raw_value=raw_str,
                    transformed_value=eparsed["value"],
                    confidence=0.7,
                    method="extracted_from_material",
                    notes="Hardness extracted from material description",
                )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.6,
            method="passthrough",
        )

    # --- Nitriding (boolean) ---
    if field_name == "Nitriding":
        norm = normalize_boolean(raw_str)
        if norm:
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=norm,
                confidence=0.95,
                method="boolean_normalize",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.5,
            method="passthrough",
        )

    # --- Nitriding type ---
    if field_name == "Nitriding type":
        match = nit_catalog.match(raw_str)
        if match.canonical:
            stats["master_data_matched"] += 1
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=match.canonical,
                confidence=match.confidence,
                method=f"master_data:{match.method}",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.4,
            method="passthrough",
        )

    # --- Nitriding depth ---
    if field_name == "Nitriding depth":
        parsed = parse_nitriding_depth(raw_str)
        if parsed and parsed != raw_str:
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=parsed,
                confidence=0.85,
                method="regex_parse",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.5,
            method="passthrough",
        )

    # --- Coating ---
    if field_name == "Coating":
        match = coat_catalog.match(raw_str)
        if match.canonical:
            stats["master_data_matched"] += 1
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=match.canonical,
                confidence=match.confidence,
                method=f"master_data:{match.method}",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.5,
            method="passthrough",
        )

    # --- Parts Group ---
    if field_name == "Parts Group":
        match = pg_catalog.match(raw_str)
        if match.canonical:
            stats["master_data_matched"] += 1
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=match.canonical,
                confidence=match.confidence,
                method=f"master_data:{match.method}",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.4,
            method="passthrough",
        )

    # --- Manufacturer (exact catalog match only — no fuzzy, DATA-003) ---
    if field_name == "Manufacturer":
        match = man_catalog.match(raw_str)
        if match.canonical:
            stats["master_data_matched"] += 1
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=match.canonical,
                confidence=match.confidence,
                method=f"master_data:{match.method}",
                notes=f"Matched to canonical manufacturer '{match.canonical}'",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=clean_text(raw_str),
            confidence=0.5,
            method="passthrough",
            notes="No manufacturer catalog match; raw value passed through",
        )

    # --- Integer fields (counts) ---
    if field_type == "integer":
        result = coerce_integer(raw_str)
        if result:
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=result,
                confidence=0.95,
                method="integer_coerce",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=raw_str,
            confidence=0.3,
            method="passthrough",
            notes="Could not coerce to integer",
        )

    # --- Decimal fields ---
    if field_type == "decimal":
        result = coerce_decimal(raw_str)
        if result:
            stats["transformed"] += 1
            return CellTransformation(
                target_field=field_name,
                target_column=field_column,
                source_column=source_col,
                raw_value=raw_str,
                transformed_value=result,
                confidence=0.9,
                method="decimal_coerce",
            )
        stats["passthrough"] += 1
        return CellTransformation(
            target_field=field_name,
            target_column=field_column,
            source_column=source_col,
            raw_value=raw_str,
            transformed_value=raw_str,
            confidence=0.3,
            method="passthrough",
        )

    # --- Default: string fields (clean text) ---
    cleaned = clean_text(raw_str)
    if cleaned != raw_str:
        stats["transformed"] += 1
        conf = 0.95
        method = "text_cleanup"
    else:
        stats["passthrough"] += 1
        conf = 0.95
        method = "passthrough"

    return CellTransformation(
        target_field=field_name,
        target_column=field_column,
        source_column=source_col,
        raw_value=raw_str,
        transformed_value=cleaned,
        confidence=conf,
        method=method,
    )
