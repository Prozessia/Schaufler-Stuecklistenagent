"""BUG-010: numeric token matching in the coordinate cross-check.

_normalize_token strips all punctuation, so "4.5"/"45" and "1-2"/"12" used to
collapse into the same string — the coordinate check then CONFIRMED (COORDOK)
values that differ from the PDF. Numeric tokens now compare on their canonical
decimal form; only true equality (incl. decimal structure) confirms.
"""

from __future__ import annotations

from src.ingestion.pdf_parser import _find_best_token_match, _normalize_numeric_token


def _row(words: list[tuple[float, float, float, float, str]]) -> dict[str, object]:
    return {
        "words": words,
        "y_min": min(w[1] for w in words),
        "y_max": max(w[3] for w in words),
    }


def _word(text: str) -> tuple[float, float, float, float, str]:
    return (5.0, 10.0, 15.0, 20.0, text)


def test_decimal_does_not_confirm_against_stripped_integer() -> None:
    """"4.5" must not be confirmed by a PDF word "45" (dot is meaning, not noise)."""
    status, _, _ = _find_best_token_match("4.5", _row([_word("45")]), None)
    assert status != "confirm"


def test_decimal_comma_variant_confirms() -> None:
    """German decimal comma is the same number: "4,5" confirms "4.5"."""
    status, matched, _ = _find_best_token_match("4.5", _row([_word("4,5")]), None)
    assert status == "confirm"
    assert matched == "4,5"


def test_plain_integer_still_confirms() -> None:
    status, _, _ = _find_best_token_match("45", _row([_word("45")]), None)
    assert status == "confirm"


def test_integer_does_not_confirm_against_dashed_token() -> None:
    """"12" must not be confirmed by "1-2" (position range vs. plain number)."""
    status, _, _ = _find_best_token_match("12", _row([_word("1-2")]), None)
    assert status != "confirm"


def test_normalize_numeric_token_shapes() -> None:
    assert _normalize_numeric_token("4,5") == "4.5"
    assert _normalize_numeric_token("4.5") == "4.5"
    assert _normalize_numeric_token("45") == "45"
    assert _normalize_numeric_token("-3") == "-3"
    assert _normalize_numeric_token("4mm") is None
    assert _normalize_numeric_token("1-2") is None
    assert _normalize_numeric_token("") is None
