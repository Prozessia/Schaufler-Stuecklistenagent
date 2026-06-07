"""Fix B: a catalog canonical that adds a variant ("1.2343" -> "1.2343 ESU") still
matches the source on the shared Werkstoffnummer — without matching a DIFFERENT one."""

from __future__ import annotations

import pytest

from src.core.statuses import MatchResult
from src.scoring.value_comparator import ValueComparator


@pytest.fixture
def comparator() -> ValueComparator:
    return ValueComparator()


def test_canonical_variant_matches_on_shared_number(comparator: ValueComparator) -> None:
    for source in ("1.2343", "g 1.2343", "1.2343 VGN", "STAHL 1.2343 +N"):
        result = comparator.compare_values(
            "1.2343 ESU", source, "Material", extraction_confidence=0.97
        )
        assert result.result == MatchResult.MATCH, (source, result.detail)


def test_different_number_still_mismatches(comparator: ValueComparator) -> None:
    """Anti-false-green: a DIFFERENT Werkstoffnummer must stay MISMATCH."""
    result = comparator.compare_values(
        "1.2343 ESU", "1.2344", "Material", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MISMATCH


def test_exact_same_value_matches(comparator: ValueComparator) -> None:
    result = comparator.compare_values(
        "1.2343 ESU", "1.2343 ESU", "Material", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MATCH


def test_no_number_does_not_invent_a_match(comparator: ValueComparator) -> None:
    """Without a shared Werkstoffnummer the fix must not fire."""
    result = comparator.compare_values(
        "Stahl blank", "Aluminium", "Material", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MISMATCH
