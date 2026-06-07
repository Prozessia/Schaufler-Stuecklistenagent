"""Value Transformer — normalizes raw BOM cell values into Schaufler target format.

Handles:
- Dimension string splitting ("832 x 950 x 527" → X=832, Y=950, Z=527)
- Hardness normalization ("43+3 HRC", "44-46 HRc" → canonical format)
- Integer coercion for count fields
- Decimal parsing with locale awareness (comma vs dot)
- Basic text cleanup (whitespace, encoding)
- Unit detection (inch → mm flagging)
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dimension parsing
# ---------------------------------------------------------------------------

# Matches patterns like "832 x 950 x 527", "832x950x527", "832 × 950 × 527",
# "Ø260 x 150", "150*200*300", etc.
_DIM_SEP = r"[\sx×\*]+[xX×\*][\sx×\*]*"  # separator between dimensions
_NUM = r"[\d]+(?:[.,]\d+)?"  # number with optional decimal

# Full 3D: "832 x 950 x 527"
_DIM_3D = re.compile(
    rf"[Øø]?\s*({_NUM})\s*[xX×\*/]\s*({_NUM})\s*[xX×\*/]\s*({_NUM})"
)
# 2D: "260 x 150" or "Ø260 x 150"
_DIM_2D = re.compile(
    rf"[Øø]?\s*({_NUM})\s*[xX×\*/]\s*({_NUM})"
)
# Single diameter: "Ø22.2" or "ø 15"
_DIM_DIAMETER = re.compile(rf"[Øø]\s*({_NUM})")

# Inch indicator patterns
_INCH_PATTERN = re.compile(r'["\u201D]|inch|in\b|zoll', re.IGNORECASE)


def parse_dimensions(value: str) -> dict[str, str | None]:
    """Parse a dimension string into X, Y, Z components.

    Returns dict with keys 'x', 'y', 'z' — values are numeric strings in
    the original unit (no conversion), or None if not present.
    Also returns 'is_inch' flag if inch indicators are detected.
    """
    if not value or not value.strip():
        return {"x": None, "y": None, "z": None, "is_inch": False}

    cleaned = value.strip()
    is_inch = bool(_INCH_PATTERN.search(cleaned))

    # Try 3D match first
    m = _DIM_3D.search(cleaned)
    if m:
        return {
            "x": _normalize_decimal(m.group(1)),
            "y": _normalize_decimal(m.group(2)),
            "z": _normalize_decimal(m.group(3)),
            "is_inch": is_inch,
        }

    # Try 2D match
    m = _DIM_2D.search(cleaned)
    if m:
        return {
            "x": _normalize_decimal(m.group(1)),
            "y": _normalize_decimal(m.group(2)),
            "z": None,
            "is_inch": is_inch,
        }

    # Try single diameter
    m = _DIM_DIAMETER.search(cleaned)
    if m:
        return {
            "x": _normalize_decimal(m.group(1)),
            "y": None,
            "z": None,
            "is_inch": is_inch,
        }

    # Try single number
    m = re.search(rf"({_NUM})", cleaned)
    if m:
        return {
            "x": _normalize_decimal(m.group(1)),
            "y": None,
            "z": None,
            "is_inch": is_inch,
        }

    return {"x": None, "y": None, "z": None, "is_inch": is_inch}


# A "pure" combined dimension is one whose ENTIRE cell is a 2D/3D measurement,
# optionally with a leading diameter sign and an optional trailing unit. Anchored
# ^...$ so it can NEVER match a thread ("M12x50"), a part code ("4x M8"), or a
# note that merely contains an "AxB" fragment ("Platte 200x100"). This is the
# guard that makes value-driven dimension splitting safe.
_PURE_DIM = re.compile(
    rf"^\s*[Øø]?\s*{_NUM}\s*[xX×\*/]\s*{_NUM}"
    rf"(?:\s*[xX×\*/]\s*{_NUM})?\s*(?:mm|MM)?\s*$"
)


def is_pure_combined_dimension(value: str | None) -> bool:
    """Return True only when the whole cell is a 2D/3D dimension like "165x74"."""
    if not value:
        return False
    return bool(_PURE_DIM.match(value.strip()))


def convert_inch_to_mm(value_str: str) -> str:
    """Convert an inch value string to mm."""
    try:
        val = float(value_str.replace(",", "."))
        mm = val * 25.4
        # Round to 1 decimal for clean output
        return f"{mm:.1f}" if mm != int(mm) else str(int(mm))
    except (ValueError, TypeError):
        return value_str


# ---------------------------------------------------------------------------
# Hardness parsing
# ---------------------------------------------------------------------------

# Patterns for hardness specifications:
# "44-46 HRC", "43+3 HRC", "46+2 HRc", "42-44", "180-200 HB",
# "45±1 HRC", "48-49HRC", "V=43.5+2HRC"
_HARDNESS_UNIT = r"(HRC|HRc|Hrc|hrc|HB|HBW|HV|Hv|N/mm[²2]|MPa)"
_HARDNESS_RANGE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*{_HARDNESS_UNIT}?"
)
_HARDNESS_PLUS = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*[+]\s*(\d+(?:[.,]\d+)?)\s*{_HARDNESS_UNIT}?"
)
_HARDNESS_PLUSMINUS = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*[±]\s*(\d+(?:[.,]\d+)?)\s*{_HARDNESS_UNIT}?"
)
_HARDNESS_SINGLE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*{_HARDNESS_UNIT}"
)


def parse_hardness(value: str) -> dict[str, str | None]:
    """Parse a hardness specification into canonical format.

    Returns dict with 'value' (canonical string like "44-46 HRC") and 'unit'.
    """
    if not value or not value.strip():
        return {"value": None, "unit": None}

    cleaned = value.strip()
    # Normalize unicode
    cleaned = unicodedata.normalize("NFKC", cleaned)

    # Extract unit if present
    unit_match = re.search(r"(HRC|HRc|Hrc|hrc|HB|HBW|HV|Hv|N/mm[²2]|MPa)", cleaned)
    unit = unit_match.group(1).upper() if unit_match else None
    if unit in ("HRC", "HRC"):
        unit = "HRC"
    elif unit in ("HB", "HBW"):
        unit = "HB"
    elif unit in ("HV", "HV"):
        unit = "HV"

    # Try ± notation: "45±1 HRC" → "44-46 HRC"
    m = _HARDNESS_PLUSMINUS.search(cleaned)
    if m:
        base = float(m.group(1).replace(",", "."))
        delta = float(m.group(2).replace(",", "."))
        unit = unit or (m.group(3).upper() if m.group(3) else None)
        lo, hi = base - delta, base + delta
        lo_s = _fmt_num(lo)
        hi_s = _fmt_num(hi)
        return {"value": f"{lo_s}-{hi_s} {unit or 'HRC'}", "unit": unit or "HRC"}

    # Try + notation: "43+3 HRC" → "43-46 HRC"
    m = _HARDNESS_PLUS.search(cleaned)
    if m:
        base = float(m.group(1).replace(",", "."))
        delta = float(m.group(2).replace(",", "."))
        unit = unit or (m.group(3).upper() if m.group(3) else None)
        lo, hi = base, base + delta
        lo_s = _fmt_num(lo)
        hi_s = _fmt_num(hi)
        return {"value": f"{lo_s}-{hi_s} {unit or 'HRC'}", "unit": unit or "HRC"}

    # Try range: "44-46 HRC"
    m = _HARDNESS_RANGE.search(cleaned)
    if m:
        lo = _fmt_num(float(m.group(1).replace(",", ".")))
        hi = _fmt_num(float(m.group(2).replace(",", ".")))
        unit = unit or (m.group(3).upper() if m.group(3) else None)
        return {"value": f"{lo}-{hi} {unit or 'HRC'}", "unit": unit or "HRC"}

    # Single value with unit: "48 HRC"
    m = _HARDNESS_SINGLE.search(cleaned)
    if m:
        val = _fmt_num(float(m.group(1).replace(",", ".")))
        unit = m.group(2).upper() if m.group(2) else unit
        return {"value": f"{val} {unit or 'HRC'}", "unit": unit or "HRC"}

    # Couldn't parse — return cleaned original
    return {"value": cleaned, "unit": unit}


# ---------------------------------------------------------------------------
# Integer / decimal coercion
# ---------------------------------------------------------------------------


def coerce_integer(value: str) -> str | None:
    """Convert value to integer string. Returns None if not parseable."""
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace(",", ".").replace(" ", "")
    # Handle things like "2.0" or "1,0"
    try:
        f = float(cleaned)
        return str(int(f))
    except (ValueError, TypeError):
        return None


def coerce_decimal(value: str) -> str | None:
    """Convert value to decimal string (dot as separator). Returns None if not parseable."""
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace(" ", "")
    # Handle European comma decimal: "832,5" → "832.5"
    # But watch out for thousand separators: "1.234,5" → "1234.5"
    if "," in cleaned and "." in cleaned:
        # Assume last separator is decimal
        if cleaned.rindex(",") > cleaned.rindex("."):
            # "1.234,5" → European: dot=thousands, comma=decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # "1,234.5" → US: comma=thousands, dot=decimal
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        f = float(cleaned)
        # Return clean format
        if f == int(f) and "." not in value.replace(",", ".").strip():
            return str(int(f))
        return f"{f:g}"
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """Normalize whitespace, strip, normalize unicode."""
    if not value:
        return ""
    # Normalize unicode (NFKC collapses compatibility chars)
    cleaned = unicodedata.normalize("NFKC", value)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_boolean(value: str) -> str | None:
    """Normalize to 'Yes' / 'No' or None if unclear."""
    if not value or not value.strip():
        return None
    low = value.strip().lower()
    _TRUE = {"yes", "ja", "si", "oui", "ano", "1", "true", "x", "y"}
    _FALSE = {"no", "nein", "non", "ne", "0", "false", "-", "n"}
    if low in _TRUE:
        return "Yes"
    if low in _FALSE:
        return "No"
    return None


# ---------------------------------------------------------------------------
# Material text pre-processing
# ---------------------------------------------------------------------------

# Pattern to extract material number from concatenated descriptions
# e.g. "STAHL DIN 4957- 1.2343 ESU V=43.5+2HRC" → "1.2343 ESU"
# or "STAHLISO 4957-1.2738 40CrMnNiMo8-6-4" → "1.2738"
_WERKSTOFF_NR = re.compile(r"(\d\.\d{4})\s*(ESU|ESR)?", re.IGNORECASE)
_WERKSTOFF_WITH_DASH = re.compile(r"(\d)[–-](\d{4})", re.IGNORECASE)


def extract_material_number(value: str) -> str | None:
    """Try to extract a Werkstoff-Nr (e.g. '1.2343') from a complex string."""
    if not value:
        return None
    # First normalize dashes: "1-2343" → try as "1.2343"
    normalized = _WERKSTOFF_WITH_DASH.sub(r"\1.\2", value)
    m = _WERKSTOFF_NR.search(normalized)
    if m:
        nr = m.group(1)
        suffix = m.group(2) or ""
        return f"{nr} {suffix}".strip() if suffix else nr
    return None


def extract_hardness_from_material(value: str) -> str | None:
    """Extract hardness spec embedded in a material/description field.

    E.g. "STAHL DIN 4957- 1.2343 ESU V=43.5+2HRC" → "43.5+2 HRC"
    """
    if not value:
        return None
    # Look for hardness-like pattern
    m = re.search(
        r"(\d+(?:[.,]\d+)?)\s*([+±])\s*(\d+(?:[.,]\d+)?)\s*(HRC|HRc|HB|HBW)",
        value,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)} {m.group(4).upper()}"
    m = re.search(
        r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(HRC|HRc|HB|HBW)",
        value,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3).upper()}"
    return None


# ---------------------------------------------------------------------------
# Nitriding depth parsing
# ---------------------------------------------------------------------------


def parse_nitriding_depth(value: str) -> str | None:
    """Parse nitriding depth to canonical 'X.X mm' format.

    Handles: "0,3 mm", "0.3mm", "0,1-0,2 mm", ".004\""
    """
    if not value or not value.strip():
        return None
    cleaned = value.strip()

    # Check for inch values
    is_inch = bool(re.search(r'["\u201D]|inch', cleaned, re.IGNORECASE))

    # Range: "0,1-0,2 mm"
    m = re.search(r"(\d+[.,]?\d*)\s*[-–]\s*(\d+[.,]?\d*)", cleaned)
    if m:
        lo = float(m.group(1).replace(",", "."))
        hi = float(m.group(2).replace(",", "."))
        if is_inch:
            lo *= 25.4
            hi *= 25.4
        return f"{lo:g}-{hi:g} mm"

    # Single: "0,3 mm" or ".004""
    m = re.search(r"\.?(\d+[.,]?\d*)", cleaned)
    if m:
        val = float(m.group(0).replace(",", "."))
        if is_inch:
            val *= 25.4
        return f"{val:g} mm"

    return cleaned


# ---------------------------------------------------------------------------
# Weight parsing
# ---------------------------------------------------------------------------


def parse_weight(value: str) -> dict[str, str | None]:
    """Parse weight value, detecting unit. Returns {'value': ..., 'unit': ...}."""
    if not value or not value.strip():
        return {"value": None, "unit": None}

    cleaned = value.strip().lower()
    unit = "kg"  # default

    if "lbs" in cleaned or "lb" in cleaned:
        unit = "lbs"
    elif "oz" in cleaned:
        unit = "oz"
    elif "g" in cleaned and "kg" not in cleaned:
        unit = "g"

    # Extract numeric
    m = re.search(r"(\d+[.,]?\d*)", cleaned)
    if m:
        num = _normalize_decimal(m.group(1))
        return {"value": num, "unit": unit}

    return {"value": None, "unit": None}


def convert_weight_to_kg(value_str: str, from_unit: str) -> str:
    """Convert weight to kg."""
    _FACTORS = {"kg": 1.0, "g": 0.001, "lbs": 0.453592, "lb": 0.453592, "oz": 0.0283495}
    try:
        val = float(value_str.replace(",", "."))
        factor = _FACTORS.get(from_unit.lower(), 1.0)
        result = val * factor
        return f"{result:g}"
    except (ValueError, TypeError):
        return value_str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_decimal(s: str) -> str:
    """Normalize a number string: comma→dot, strip whitespace."""
    return s.strip().replace(",", ".")


def _fmt_num(val: float) -> str:
    """Format number: drop .0 for integers."""
    if val == int(val):
        return str(int(val))
    return f"{val:g}"
