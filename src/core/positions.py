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
    dashes so ``"1 - 2"``, ``" K-3 "`` and ``"1.0"`` compare consistently
    everywhere they are matched.
    """
    if value is None:
        return ""
    normalized = " ".join(str(value).strip().upper().split())
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    return normalized
