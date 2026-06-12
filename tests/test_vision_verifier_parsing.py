"""Unit tests for _safe_json_loads and _normalize_for_compare (BUG-012)."""

from __future__ import annotations

from src.scoring.vision_verifier import _normalize_for_compare, _safe_json_loads


# ---------------------------------------------------------------------------
# _safe_json_loads
# ---------------------------------------------------------------------------


def test_safe_json_loads_strips_json_fence() -> None:
    raw = "```json\n{\"status\": \"found\", \"confidence\": 0.9}\n```"
    result = _safe_json_loads(raw)
    assert result is not None
    assert result["status"] == "found"


def test_safe_json_loads_strips_plain_fence() -> None:
    raw = "```\n{\"a\": 1}\n```"
    result = _safe_json_loads(raw)
    assert result is not None
    assert result["a"] == 1


def test_safe_json_loads_repairs_trailing_comma() -> None:
    raw = '{"a": 1,}'
    result = _safe_json_loads(raw)
    assert result is not None
    assert result["a"] == 1


def test_safe_json_loads_extracts_json_from_surrounding_text() -> None:
    raw = 'Here is the answer: {"status": "not_found"} — done.'
    result = _safe_json_loads(raw)
    assert result is not None
    assert result["status"] == "not_found"


def test_safe_json_loads_returns_none_for_empty() -> None:
    assert _safe_json_loads("") is None
    assert _safe_json_loads("   ") is None


# ---------------------------------------------------------------------------
# _normalize_for_compare
# ---------------------------------------------------------------------------


def test_normalize_for_compare_comma_dot_equivalence() -> None:
    assert _normalize_for_compare("4,5") == _normalize_for_compare("4.5")


def test_normalize_for_compare_collapses_whitespace() -> None:
    assert _normalize_for_compare("A  B") == "a b"


def test_normalize_for_compare_case_fold() -> None:
    assert _normalize_for_compare("ABC") == "abc"


def test_normalize_for_compare_integer_unchanged() -> None:
    assert _normalize_for_compare("42") == "42"
