"""Mapping Validator — rule-based validation of LLM mapping results.

Applies deterministic checks to catch obvious mapping errors that
the LLM might make, and adjusts confidence scores accordingly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.core.models import ParsedBOM
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetSchema

logger = logging.getLogger(__name__)

# Fix C/D: value-evidence mapping-confidence boost. When a column's VALUES strongly
# confirm the target field's type (the "Vergütung" column is full of Werkstoff-
# numbers → it IS the material column, regardless of a misleading header), raise
# the mapping confidence so the value-verified cells can reach GREEN. Self-
# correcting and safe: a wrongly-mapped column has no type-matching values → no
# boost. This RAISES confidence on evidence; it does NOT lower the green bar.
_COMBINED_DIM_RE = re.compile(r"\d+[.,]?\d*\s*[xX×*]\s*\d+")
# Boost fires only when this fraction of a column's values confirm the target type.
# Deliberately not too low: messy/mis-extracted columns (e.g. Magna's broken
# multiline dimensions) score low and are correctly NOT boosted — self-correcting.
_VALUE_EVIDENCE_MIN_FRACTION = 0.40
# BUG-007: deliberately BELOW the 0.90 green bar. Value evidence may turn a weak
# YELLOW into a confident YELLOW, but must never single-handedly unlock GREEN —
# after the full Stammdaten import even a Norm column ("DIN 16756", "EN 10088")
# resolves heavily against the catalog and would otherwise clear the bar.
_VALUE_EVIDENCE_BOOST = 0.89
_DIMENSION_FIELDS_ORDERED = ("Dimensions X/D", "Dimensions Y/L", "Dimensions Z")


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: str  # "error", "warning", "info"
    message: str
    source_column: str = ""
    target_field: str = ""


@dataclass
class ValidationResult:
    """Complete validation output."""

    issues: list[ValidationIssue] = field(default_factory=list)
    adjusted_mappings: list[ColumnMapping] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0

    @property
    def blocking_by_target(self) -> dict[str, list[str]]:
        blocked: dict[str, list[str]] = {}
        for issue in self.issues:
            if issue.severity != "error" or not issue.target_field:
                continue
            blocked.setdefault(issue.target_field, []).append(issue.message)
        return blocked

    def blocking_for_target(self, target_field: str) -> list[str]:
        return self.blocking_by_target.get(target_field, [])


# ---------------------------------------------------------------------------
# Type-compatibility heuristics
# ---------------------------------------------------------------------------

_NUMERIC_PATTERNS = re.compile(r"^[\d.,\s\-+±/xX×Øø°]+$")

_INTEGER_PATTERNS = re.compile(r"^\d+$")

_SPARE_HEADER_HINTS = re.compile(r"spare|ersatz|reserve|backup", re.IGNORECASE)
_QTY_HEADER_HINTS = re.compile(
    r"qty|quantity|anzahl|menge|stk|stck|count", re.IGNORECASE
)
_CUSTOMER_HINTS = re.compile(r"customer|kunden|cust|client", re.IGNORECASE)
_MANUFACTURER_HINTS = re.compile(
    r"manufacturer|hersteller|supplier|lieferant|vendor",
    re.IGNORECASE,
)
_MATERIAL_CODE = re.compile(r"\b\d\.\d{4}\b")


def _looks_numeric(values: list[str]) -> bool:
    """Check if sample values look like numbers."""
    non_empty = [v for v in values if v and v.strip()]
    if not non_empty:
        return False
    numeric_count = sum(1 for v in non_empty if _NUMERIC_PATTERNS.match(v.strip()))
    return numeric_count / len(non_empty) >= 0.6


def _looks_integer(values: list[str]) -> bool:
    """Check if sample values look like integers."""
    non_empty = [v for v in values if v and v.strip()]
    if not non_empty:
        return False
    int_count = sum(1 for v in non_empty if _INTEGER_PATTERNS.match(v.strip()))
    return int_count / len(non_empty) >= 0.6


def _get_sample_values(bom: ParsedBOM, column: str, max_rows: int = 10) -> list[str]:
    """Extract sample values for a column from the BOM."""
    values = []
    for row in bom.rows[:max_rows]:
        val = row.get(column)
        if val is not None:
            values.append(str(val).strip())
    return values


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


def validate_mapping(
    mapping_result: MappingResult,
    bom: ParsedBOM,
    schema: TargetSchema,
) -> ValidationResult:
    """Validate a mapping result and adjust confidence scores.

    Checks:
    1. No duplicate target assignments
    2. Required target fields should be mapped
    3. Type compatibility (numeric source → numeric target)
    4. Confidence reasonableness
    """
    issues: list[ValidationIssue] = []
    adjusted = list(mapping_result.mappings)  # shallow copy of list

    # --- Check 1: Duplicate target assignments ---
    target_assignments: dict[str, list[ColumnMapping]] = {}
    for m in adjusted:
        if m.target_field:
            target_assignments.setdefault(m.target_field, []).append(m)

    for target, sources in target_assignments.items():
        if len(sources) > 1:
            # Keep the one with highest confidence, demote others
            sorted_sources = sorted(sources, key=lambda x: x.confidence, reverse=True)
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=(
                        f"Target field '{target}' mapped from multiple sources: "
                        f"{[s.source_column for s in sorted_sources]}. "
                        f"Keeping '{sorted_sources[0].source_column}', demoting others."
                    ),
                    target_field=target,
                )
            )
            # Demote all but the best
            for loser in sorted_sources[1:]:
                loser.target_field = None
                loser.target_column = None
                loser.confidence = max(loser.confidence * 0.3, 0.0)
                loser.reasoning += " [DEMOTED: duplicate target assignment]"

    # --- Check 2: Required fields coverage ---
    mapped_targets = {m.target_field for m in adjusted if m.target_field}
    for req_field in schema.required_fields:
        if req_field.name not in mapped_targets:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"Required target field '{req_field.name}' (Col {req_field.column}) has no source mapping",
                    target_field=req_field.name,
                )
            )

    # --- Check 3: Type compatibility ---
    field_lookup = schema.field_by_name
    for m in adjusted:
        if not m.target_field or m.target_field not in field_lookup:
            continue

        target_def = field_lookup[m.target_field]
        samples = _get_sample_values(bom, m.source_column)

        if target_def.type == "integer":
            if samples and not _looks_integer(samples) and not _looks_numeric(samples):
                penalty = 0.15
                m.confidence = max(m.confidence - penalty, 0.0)
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=(
                            f"Source '{m.source_column}' mapped to integer field "
                            f"'{m.target_field}' but samples don't look numeric: "
                            f"{samples[:3]}"
                        ),
                        source_column=m.source_column,
                        target_field=m.target_field,
                    )
                )

        if target_def.type == "decimal":
            # A dimension field is fed a COMBINED string ("2300x2080x563") that the
            # transform splits into decimals — that is valid, not a type error.
            is_combined_dimension = (
                m.target_field in _DIMENSION_FIELDS_ORDERED
                and any(_COMBINED_DIM_RE.search(s) for s in samples)
            )
            if samples and not _looks_numeric(samples) and not is_combined_dimension:
                penalty = 0.1
                m.confidence = max(m.confidence - penalty, 0.0)
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=(
                            f"Source '{m.source_column}' mapped to decimal field "
                            f"'{m.target_field}' but samples don't look numeric: "
                            f"{samples[:3]}"
                        ),
                        source_column=m.source_column,
                        target_field=m.target_field,
                    )
                )

    # --- Check 4: Very low confidence mappings ---
    for m in adjusted:
        if m.target_field and m.confidence < 0.3:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message=(
                        f"Very low confidence ({m.confidence:.2f}) for mapping "
                        f"'{m.source_column}' → '{m.target_field}'"
                    ),
                    source_column=m.source_column,
                    target_field=m.target_field,
                )
            )

    # --- Check 5: High-confidence sanity for dimension fields ---
    dim_fields = {"Dimensions X/D", "Dimensions Y/L", "Dimensions Z"}
    dim_mappings = [m for m in adjusted if m.target_field in dim_fields]
    if len(dim_mappings) == 1:
        # Only one dimension mapped — likely a combined field, cap confidence
        m = dim_mappings[0]
        if m.confidence > 0.8:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message=(
                        f"Single dimension mapping '{m.source_column}' → '{m.target_field}'. "
                        f"Source likely contains combined X×Y×Z — needs splitting in transform layer."
                    ),
                    source_column=m.source_column,
                    target_field=m.target_field,
                )
            )

    # --- Check 6: Design vs Spare count ambiguity ---
    design_mapping = next(
        (m for m in adjusted if m.target_field == "Design Count"), None
    )
    spare_mapping = next((m for m in adjusted if m.target_field == "Spare Count"), None)
    if design_mapping:
        has_spare_source = any(_SPARE_HEADER_HINTS.search(h or "") for h in bom.headers)
        source_looks_qty = bool(
            _QTY_HEADER_HINTS.search(design_mapping.source_column or "")
        )
        if has_spare_source and source_looks_qty and spare_mapping is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=(
                        "Potential count ambiguity: Design Count mapped while spare-like "
                        "source headers exist but Spare Count is unmapped"
                    ),
                    source_column=design_mapping.source_column,
                    target_field="Design Count",
                )
            )

    # --- Check 7: Customer vs Manufacturer swap ---
    for m in adjusted:
        if not m.target_field:
            continue

        source_header = m.source_column or ""
        if m.target_field == "Customer Part Number" and _MANUFACTURER_HINTS.search(
            source_header
        ):
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=(
                        "Potential semantic swap: Customer Part Number mapped from "
                        f"manufacturer-like source column '{source_header}'"
                    ),
                    source_column=source_header,
                    target_field=m.target_field,
                )
            )

        if m.target_field == "Manufacturer part no." and _CUSTOMER_HINTS.search(
            source_header
        ):
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=(
                        "Potential semantic swap: Manufacturer part no. mapped from "
                        f"customer-like source column '{source_header}'"
                    ),
                    source_column=source_header,
                    target_field=m.target_field,
                )
            )

    # --- Check 8: Material code ambiguity in source samples ---
    material_mapping = next((m for m in adjusted if m.target_field == "Material"), None)
    if material_mapping:
        material_samples = _get_sample_values(
            bom, material_mapping.source_column, max_rows=20
        )
        codes = sorted(
            {
                match.group(0)
                for sample in material_samples
                for match in _MATERIAL_CODE.finditer(sample)
            }
        )
        if len(codes) >= 2:
            # INFO, not warning: a material column naturally holds many distinct
            # Werkstoff codes (different parts → different materials). As a warning
            # this capped EVERY real material column to YELLOW (it can never be
            # green). Kept as info for visibility; it must not gate the traffic light.
            issues.append(
                ValidationIssue(
                    severity="info",
                    message=(
                        "Material source contains multiple distinct Werkstoff codes "
                        f"{codes[:4]} (normal for a material column)"
                    ),
                    source_column=material_mapping.source_column,
                    target_field="Material",
                )
            )

    # Fix C/D: raise confidence where the column's values confirm the target type,
    # and give the split dimension components (Y/L, Z) the same mapping as the
    # combined source so they are not left unmapped (candidate_confidence 0.0).
    adjusted = _apply_value_evidence(adjusted, bom, schema)

    return ValidationResult(issues=issues, adjusted_mappings=adjusted)


def _column_values(bom: ParsedBOM, source_column: str) -> list[str]:
    return [
        value
        for row in bom.rows
        if (value := str(row.get(source_column) or "").strip())
    ]


def _value_evidence_fraction(values: list[str], pattern: re.Pattern[str]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if pattern.search(v)) / len(values)


def _material_evidence_fraction(values: list[str]) -> float:
    """Fraction of values that resolve to a real material (catalog or DIN number).

    A wrongly-mapped column (descriptions, order numbers) resolves to ~0 → no boost.
    """
    if not values:
        return 0.0
    from src.transform.master_data_matcher import get_material_catalog

    catalog = get_material_catalog()
    hits = sum(
        1 for v in values if catalog.match(v).method not in ("no_match", "empty")
    )
    return hits / len(values)


def _apply_value_evidence(
    adjusted: list[ColumnMapping],
    bom: ParsedBOM,
    schema: TargetSchema,
) -> list[ColumnMapping]:
    """Boost mapping confidence on value evidence + propagate combined dimensions."""
    for m in adjusted:
        if not m.source_column:
            continue
        values = _column_values(bom, m.source_column)
        if m.target_field == "Material":
            # Catalog-based: counts Werkstoffnummern AND DIN names / aliases, so a
            # material column heavy on DIN names (X38CrMoV5-1) is still recognised.
            fraction = _material_evidence_fraction(values)
        elif m.target_field in _DIMENSION_FIELDS_ORDERED:
            fraction = _value_evidence_fraction(values, _COMBINED_DIM_RE)
        else:
            continue
        if fraction >= _VALUE_EVIDENCE_MIN_FRACTION:
            m.candidate_confidence = max(m.candidate_confidence, _VALUE_EVIDENCE_BOOST)
            m.confidence = max(m.confidence, _VALUE_EVIDENCE_BOOST)
            m.reasoning = (m.reasoning or "") + f" [VALUE-CONFIRMED {fraction:.0%}]"

    # Propagate a single combined-dimension mapping to the missing components, so
    # the transform's split values (Y/L, Z) inherit the mapping confidence instead
    # of being scored as unmapped. Only when the source values ARE combined dims.
    by_target = {m.target_field: m for m in adjusted}
    mapped_dims = [d for d in _DIMENSION_FIELDS_ORDERED if d in by_target]
    if len(mapped_dims) == 1:
        primary = by_target[mapped_dims[0]]
        values = _column_values(bom, primary.source_column)
        if (
            primary.source_column
            and _value_evidence_fraction(values, _COMBINED_DIM_RE)
            >= _VALUE_EVIDENCE_MIN_FRACTION
        ):
            for field_name in _DIMENSION_FIELDS_ORDERED:
                if field_name in by_target:
                    continue
                field_def = schema.field_by_name.get(field_name)
                if field_def is None:
                    continue
                adjusted.append(
                    ColumnMapping(
                        source_column=primary.source_column,
                        target_field=field_name,
                        target_column=field_def.column,
                        confidence=primary.confidence,
                        reasoning="[dimension split component of "
                        f"'{primary.source_column}']",
                        candidate_confidence=primary.candidate_confidence,
                        candidate_reasoning="dimension split component",
                    )
                )

    return adjusted
