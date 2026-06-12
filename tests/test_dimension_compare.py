"""Fix A: a dimension component split from a combined source string matches the
numeric token at its position — without ever green-lighting a wrong component."""

from __future__ import annotations

import pytest

from src.core.statuses import MatchResult
from src.scoring.value_comparator import ValueComparator


@pytest.fixture
def comparator() -> ValueComparator:
    return ValueComparator()


def test_component_matches_its_position(comparator: ValueComparator) -> None:
    for field, value in [
        ("Dimensions X/D", "2300"),
        ("Dimensions Y/L", "2080"),
        ("Dimensions Z", "563"),
    ]:
        result = comparator.compare_values(
            value, "2300x2080x563", field, extraction_confidence=0.97
        )
        assert result.result == MatchResult.MATCH, (field, value, result.detail)


def test_german_comma_components_match(comparator: ValueComparator) -> None:
    result = comparator.compare_values(
        "450,0", "761,0x450,0x100,0", "Dimensions Y/L", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MATCH


def test_wrong_component_value_still_mismatches(comparator: ValueComparator) -> None:
    """Anti-false-green: a wrongly-assigned component is a DEFINITIVE mismatch.

    The positional check compares the mapped value against the token at its
    component position; a contradiction is hard MISMATCH (veto), not UNCERTAIN —
    UNCERTAIN would let the text path promote the cell to GREEN.
    """
    result = comparator.compare_values(
        "999", "2300x2080x563", "Dimensions Y/L", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MISMATCH


def test_positional_only_fires_for_combined_strings(comparator: ValueComparator) -> None:
    """A scalar source (one number) goes through the normal decimal compare; the
    positional rule needs >= 2 tokens, so it cannot invent a match here."""
    result = comparator.compare_values(
        "563", "999", "Dimensions Z", extraction_confidence=0.97
    )
    assert result.result == MatchResult.MISMATCH


def test_positional_needs_high_confidence(comparator: ValueComparator) -> None:
    """Low extraction confidence must not earn the positional match.

    After BUG-009 (_parse_decimal uses fullmatch), compound strings like
    "2300x2080x563" no longer partially parse as a scalar. At low confidence the
    component AGREES with its position, but MATCH stays gated on confidence —
    the result is UNCERTAIN (review), never MATCH.
    """
    result = comparator.compare_values(
        "2080", "2300x2080x563", "Dimensions Y/L", extraction_confidence=0.5
    )
    assert result.result == MatchResult.UNCERTAIN
