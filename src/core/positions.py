"""Canonical position-identifier helpers — single source of truth (AW-2).

Position normalisation and the set of position-carrying target fields used to be
duplicated across the reconciler, the scorer and the parser. The B2/B3
zero-data-loss guarantee only holds while the reconciler and the scorer normalise
identically; if one copy drifts, positions silently fail to match (phantom RED or
a real loss slips through). Therefore the normaliser lives here and every consumer
imports it.
"""

from __future__ import annotations

import re

# Position-carrying target fields, in priority order.
POSITION_FIELDS: tuple[str, ...] = ("Detail Number", "Position")


def normalize_position(value: str | None) -> str:
    """Normalise a position identifier for set comparison across the pipeline.

    Upper-cases, collapses internal whitespace, and tightens spacing around
    dashes.  Additionally:

    * ``"1.0"`` / ``"12.00"`` (decimal with only trailing zeros) → ``"1"`` /
      ``"12"``.  Sub-positions like ``"1.2"`` or ``"1.10"`` are left unchanged.
    * ``"007"`` (integer with leading zeros) → ``"7"``.  ``"0"`` stays ``"0"``.
    """
    if value is None:
        return ""
    normalized = " ".join(str(value).strip().upper().split())
    normalized = re.sub(r"\s*-\s*", "-", normalized)

    # Trailing-zero decimal collapse: "1.0" → "1", "12.00" → "12".
    # Sub-positions ("1.2", "1.10") are NOT touched.
    _trailing_zero_re = re.compile(r"^(\d+)\.0+$")
    m = _trailing_zero_re.match(normalized)
    if m:
        normalized = m.group(1)

    # Leading-zero integer strip: "007" → "7".  "0" stays "0".
    _leading_zero_re = re.compile(r"^0+(\d+)$")
    m2 = _leading_zero_re.match(normalized)
    if m2:
        normalized = m2.group(1)

    return normalized
