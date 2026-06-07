"""Cross Validator — plausibility checks across transformed BOM values.

Checks:
- Material ↔ Hardness compatibility (is the hardness in range for the material?)
- Dimension plausibility (reasonable size for die casting tooling)
- Count fields (Design Count > 0, Spare Count >= 0)
- Required fields present
- Duplicate detail numbers
"""

from __future__ import annotations

import logging
import re

from src.core.models import CellTransformation, TransformedRow, TransformationResult
from src.transform.master_data_matcher import get_material_catalog

logger = logging.getLogger(__name__)


class ValidationIssue:
    """A single cross-validation finding."""

    __slots__ = ("severity", "row_index", "field", "message")

    def __init__(self, severity: str, row_index: int, field: str, message: str) -> None:
        self.severity = severity  # "error", "warning", "info"
        self.row_index = row_index
        self.field = field
        self.message = message

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] Row {self.row_index}, {self.field}: {self.message}"


class CrossValidationResult:
    """Aggregated validation results."""

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def add(self, severity: str, row_index: int, field: str, message: str) -> None:
        self.issues.append(ValidationIssue(severity, row_index, field, message))

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")

    def summary(self) -> str:
        return (
            f"Cross-validation: {self.error_count} errors, "
            f"{self.warning_count} warnings, {self.info_count} info"
        )


def cross_validate(result: TransformationResult) -> CrossValidationResult:
    """Run all cross-validation checks on a TransformationResult."""
    cv = CrossValidationResult()
    _check_required_fields(result, cv)
    _check_duplicate_detail_numbers(result, cv)
    for row in result.rows:
        _check_dimension_plausibility(row, cv)
        _check_material_hardness(row, cv)
        _check_finish_vs_rough_dimensions(row, cv)
        _check_weight_density(row, cv)
        _check_counts(row, cv)
    return cv


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_required_fields(
    result: TransformationResult, cv: CrossValidationResult
) -> None:
    """Check that required target fields have values."""
    required = ["Detail Number", "Design Count", "Description"]
    for row in result.rows:
        for field_name in required:
            cell = row.get_cell(field_name)
            if cell is None or not cell.transformed_value:
                cv.add("warning", row.row_index, field_name, "Required field is empty")


def _check_duplicate_detail_numbers(
    result: TransformationResult, cv: CrossValidationResult
) -> None:
    """Check for duplicate detail/position numbers."""
    seen: dict[str, int] = {}
    for row in result.rows:
        cell = row.get_cell("Detail Number")
        if cell and cell.transformed_value:
            val = cell.transformed_value.strip()
            if val in seen:
                cv.add(
                    "warning",
                    row.row_index,
                    "Detail Number",
                    f"Duplicate: '{val}' also in row {seen[val]}",
                )
            else:
                seen[val] = row.row_index


def _check_dimension_plausibility(
    row: TransformedRow, cv: CrossValidationResult
) -> None:
    """Check dimension values are in reasonable range for die casting tooling."""
    # Typical range: 1 mm to 5000 mm for tooling components
    for dim_field in ("Dimensions X/D", "Dimensions Y/L", "Dimensions Z"):
        cell = row.get_cell(dim_field)
        if cell and cell.transformed_value:
            try:
                val = float(cell.transformed_value.replace(",", "."))
                if val < 0.1:
                    cv.add("warning", row.row_index, dim_field, f"Very small: {val} mm")
                elif val > 5000:
                    cv.add("warning", row.row_index, dim_field, f"Very large: {val} mm")
            except (ValueError, TypeError):
                pass


def _check_material_hardness(row: TransformedRow, cv: CrossValidationResult) -> None:
    """Check if hardness specification is compatible with the given material."""
    mat_cell = row.get_cell("Material")
    hard_cell = row.get_cell("Hardness")
    if not mat_cell or not mat_cell.transformed_value:
        return
    if not hard_cell or not hard_cell.transformed_value:
        return

    parsed_hardness = _parse_hardness_range(hard_cell.transformed_value)
    if parsed_hardness is not None:
        lo, hi, unit = parsed_hardness
        if (
            unit == "HRC"
            and _is_aluminum_material(mat_cell.transformed_value)
            and hi > 40
        ):
            cv.add(
                "error",
                row.row_index,
                "Hardness",
                "ENG_CONTRADICTION: Aluminum material with hardness above 40 HRC",
            )

    catalog = get_material_catalog()
    match = catalog.match(mat_cell.transformed_value)
    if not match.canonical:
        return

    expected_range = catalog.get_hardness_range(match.canonical)
    if not expected_range:
        return  # No hardness data for this material

    if parsed_hardness is None:
        return
    lo, hi, unit = parsed_hardness

    # Only compare HRC values against HRC material ranges.
    if unit != "HRC":
        return

    expected_lo, expected_hi = expected_range
    # Allow some tolerance (±5 HRC)
    if hi < expected_lo - 5:
        cv.add(
            "info",
            row.row_index,
            "Hardness",
            f"Hardness {hard_cell.transformed_value} below typical range "
            f"({expected_lo}-{expected_hi} HRC) for {mat_cell.transformed_value}",
        )
    elif lo > expected_hi + 5:
        cv.add(
            "warning",
            row.row_index,
            "Hardness",
            f"Hardness {hard_cell.transformed_value} above typical range "
            f"({expected_lo}-{expected_hi} HRC) for {mat_cell.transformed_value}",
        )


def _check_counts(row: TransformedRow, cv: CrossValidationResult) -> None:
    """Check that count fields have valid integer values."""
    for field_name in ("Design Count", "Spare Count"):
        cell = row.get_cell(field_name)
        if cell and cell.transformed_value:
            try:
                val = int(cell.transformed_value)
                if field_name == "Design Count" and val < 1:
                    cv.add(
                        "warning",
                        row.row_index,
                        field_name,
                        f"Design Count should be >= 1, got {val}",
                    )
                if val < 0:
                    cv.add("error", row.row_index, field_name, f"Negative count: {val}")
            except (ValueError, TypeError):
                cv.add(
                    "warning",
                    row.row_index,
                    field_name,
                    f"Not an integer: '{cell.transformed_value}'",
                )


def _check_finish_vs_rough_dimensions(
    row: TransformedRow, cv: CrossValidationResult
) -> None:
    """Check that finished dimensions are not larger than rough dimensions."""
    dims = {c.target_field.lower(): c for c in row.cells if c.transformed_value}
    for axis_token in ("x", "y", "z"):
        finish_cell = _find_dimension_cell(dims, axis_token, rough=False)
        rough_cell = _find_dimension_cell(dims, axis_token, rough=True)
        if not finish_cell or not rough_cell:
            continue

        finish_val = _parse_decimal(finish_cell.transformed_value)
        rough_val = _parse_decimal(rough_cell.transformed_value)
        if finish_val is None or rough_val is None:
            continue

        if finish_val > rough_val:
            cv.add(
                "error",
                row.row_index,
                finish_cell.target_field,
                (
                    "ENG_CONTRADICTION: Finished dimension larger than rough dimension "
                    f"({finish_val} > {rough_val})"
                ),
            )


def _check_weight_density(row: TransformedRow, cv: CrossValidationResult) -> None:
    """Coarse mass-volume-density plausibility check for gross contradictions."""
    weight_cell = next(
        (
            c
            for c in row.cells
            if c.transformed_value
            and any(
                k in c.target_field.lower()
                for k in ("weight", "gewicht", "mass", "masse")
            )
        ),
        None,
    )
    mat_cell = row.get_cell("Material")
    if not weight_cell or not mat_cell or not mat_cell.transformed_value:
        return

    x = _parse_decimal(
        (
            row.get_cell("Dimensions X/D") or CellTransformation(target_field="")
        ).transformed_value
    )
    y = _parse_decimal(
        (
            row.get_cell("Dimensions Y/L") or CellTransformation(target_field="")
        ).transformed_value
    )
    z = _parse_decimal(
        (
            row.get_cell("Dimensions Z") or CellTransformation(target_field="")
        ).transformed_value
    )
    if x is None or y is None or z is None:
        return

    weight_kg = _parse_weight_to_kg(weight_cell.transformed_value)
    if weight_kg is None:
        return

    density = _material_density_g_cm3(mat_cell.transformed_value)
    if density is None:
        return

    volume_cm3 = (x * y * z) / 1000.0  # mm^3 -> cm^3
    expected_kg = (density * volume_cm3) / 1000.0
    if expected_kg <= 0:
        return

    ratio = weight_kg / expected_kg
    if ratio < 0.1 or ratio > 10.0:
        cv.add(
            "error",
            row.row_index,
            weight_cell.target_field,
            (
                "ENG_CONTRADICTION: Weight-volume-density mismatch "
                f"(actual={weight_kg:.3f}kg, expected≈{expected_kg:.3f}kg)"
            ),
        )


def _find_dimension_cell(
    dims: dict[str, CellTransformation],
    axis_token: str,
    rough: bool,
) -> CellTransformation | None:
    """Find dimension cell for an axis, optionally restricted to rough fields."""
    axis_markers = {
        "x": ("x/d", " x", "d"),
        "y": ("y/l", " y", "l"),
        "z": ("z",),
    }
    for name, cell in dims.items():
        is_dim = "dimension" in name or "maß" in name or "mass" in name
        is_rough = any(k in name for k in ("rough", "roh"))
        if not is_dim:
            continue
        if rough != is_rough:
            continue
        if any(marker in f" {name}" for marker in axis_markers[axis_token]):
            return cell
    return None


def _parse_decimal(value: str | None) -> float | None:
    if not value:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", value)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_hardness_range(value: str) -> tuple[float, float, str] | None:
    """Parse hardness string to (lo, hi, unit)."""
    text = value.strip()
    unit = "HB" if "HB" in text.upper() else "HRC"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)", text)
    if m:
        lo = float(m.group(1).replace(",", "."))
        hi = float(m.group(2).replace(",", "."))
        return lo, hi, unit

    s = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not s:
        return None
    v = float(s.group(1).replace(",", "."))
    return v, v, unit


def _parse_weight_to_kg(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip().lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", "."))
    if "g" in text and "kg" not in text:
        return num / 1000.0
    return num


def _material_density_g_cm3(material: str) -> float | None:
    m = material.lower()
    if any(k in m for k in ("alu", "aluminium", "alsi")):
        return 2.7
    if any(k in m for k in ("cu", "kupfer", "copper", "be-cu", "beryllium")):
        return 8.9
    if any(k in m for k in ("stahl", "steel", "h13", "p20", "1.", "dievar")):
        return 7.8
    return None


def _is_aluminum_material(material: str) -> bool:
    m = material.lower()
    return any(k in m for k in ("alu", "aluminium", "alsi"))
