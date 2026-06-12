"""Unit tests for normalize_position (BUG-015)."""

from __future__ import annotations

import pytest

from src.core.positions import normalize_position


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Trailing-zero decimal collapse
        ("1.0", "1"),
        ("12.00", "12"),
        # Sub-positions must NOT be collapsed
        ("1.2", "1.2"),
        ("1.10", "1.10"),
        # Leading-zero integer strip
        ("007", "7"),
        ("0", "0"),
        # Alphanumeric — unchanged
        ("K-3", "K-3"),
        # Whitespace / dash tightening
        (" 1 - 2 ", "1-2"),
    ],
)
def test_normalize_position_table(raw: str, expected: str) -> None:
    assert normalize_position(raw) == expected


def test_normalize_position_none_returns_empty() -> None:
    assert normalize_position(None) == ""
