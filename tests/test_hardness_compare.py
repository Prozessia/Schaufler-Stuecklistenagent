"""Fix (Hardness): same hardness value with a different unit word or word order
matches on the shared numeric range — without matching a DIFFERENT hardness."""

from __future__ import annotations

import pytest

from src.core.statuses import MatchResult
from src.scoring.value_comparator import ValueComparator


@pytest.fixture
def comparator() -> ValueComparator:
    return ValueComparator()


def test_word_order_and_unit_suffix_match(comparator: ValueComparator) -> None:
    for mapped, source in [
        ("31-35 HRC", "HRC 31-35"),
        ("470-630 HRC", "470-630"),
        ("48 HRC", "48"),
        ("0.2-0.3 HRC", "1.2311 / 0.2-0.3"),
    ]:
        result = comparator.compare_values(
            mapped, source, "Hardness", extraction_confidence=0.97
        )
        assert result.result == MatchResult.MATCH, (mapped, source, result.detail)


def test_different_hardness_range_mismatches(comparator: ValueComparator) -> None:
    """Anti-false-green: a different range shares no core → MISMATCH."""
    result = comparator.compare_values(
        "31-35 HRC", "40-44 HRC", "Hardness", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MISMATCH


def test_material_number_in_hardness_does_not_match_range(
    comparator: ValueComparator,
) -> None:
    """A material number bled into the source must not match a hardness range."""
    result = comparator.compare_values(
        "31-35 HRC", "1.2343", "Hardness", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MISMATCH
