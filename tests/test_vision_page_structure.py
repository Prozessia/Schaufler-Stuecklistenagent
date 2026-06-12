"""BUG-017 acceptance: per-page schema re-detection for anomalous pages.

When page >= 2 produces only empty/sparse rows under the page-1 schema,
_extract_all_pages_via_vision re-runs Phase A for that page and re-extracts
using the new schema when the column sets differ sufficiently (Jaccard < 0.5).
"""

from __future__ import annotations

import json

import pytest

import src.ingestion.pdf_parser as pdf_parser
from src.llm.base import LLMResponse

# ---------------------------------------------------------------------------
# Schema definitions used in the tests
# ---------------------------------------------------------------------------

_SCHEMA_A = ["POS", "BENENNUNG", "STK"]  # page-1 schema
_SCHEMA_B = ["ITEM", "DESCRIPTION", "QTY", "MATERIAL"]  # page-2 schema (very different)


def _llm_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        tokens_input=10,
        tokens_output=10,
        model="mock",
        latency_ms=1.0,
    )


def _phase_a_response(columns: list[str]) -> str:
    return json.dumps({"columns": columns})


def _phase_b_rows_response(rows: list[dict[str, str | None]]) -> str:
    return json.dumps({"rows": rows})


class _ScriptedLLM:
    """Mock LLM that serves pre-configured responses in sequence.

    Each call to complete_with_image() pops the next response from the queue.
    Records every call so tests can assert on call count and order.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.image_calls: int = 0
        self.recorded_users: list[str] = []

    async def complete_with_image(self, **kwargs: object) -> LLMResponse:
        self.image_calls += 1
        user = str(kwargs.get("user", ""))
        self.recorded_users.append(user)
        if not self._responses:
            # Return empty rows as a safety fallback
            return _llm_response(_phase_b_rows_response([]))
        return _llm_response(self._responses.pop(0))


# ---------------------------------------------------------------------------
# Test 1: page 2 anomalous → re-detect triggers, schema B rows included
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anomalous_page2_redetects_and_uses_new_schema() -> None:
    """BUG-017: page 2 yields only empty rows under schema A → re-detect fires.

    Sequence of LLM calls:
      [0] Phase B-A page 1 → 1 real row (schema A)
      [1] Phase B-B page 1 → 1 real row (schema A)
      [2] Phase B-A page 2 → 0 rows (all None — anomaly trigger)
      [3] Phase B-B page 2 → 0 rows
      [4] Phase A re-detect page 2 → schema B columns
      [5] Phase B-A page 2 retry with schema B → 1 real row
      [6] Phase B-B page 2 retry with schema B → 1 real row

    Expected result: output contains page-1 row (schema A) + page-2 row (schema B),
    and detected_columns is extended with schema B names.
    """
    page1_row_a = {"POS": "1", "BENENNUNG": "PLATE", "STK": "2"}
    page1_row_b = {"POS": "1", "BENENNUNG": "PLATE", "STK": "2"}

    # Page 2 with schema A: all columns are None → anomaly
    page2_empty_a: list[dict] = []  # no rows extracted
    page2_empty_b: list[dict] = []

    # Re-extracted page 2 with schema B
    page2_row_a = {"ITEM": "10", "DESCRIPTION": "CORE INSERT", "QTY": "1", "MATERIAL": "1.2343"}
    page2_row_b = {"ITEM": "10", "DESCRIPTION": "CORE INSERT", "QTY": "1", "MATERIAL": "1.2343"}

    responses = [
        # Page 1: Phase B-A and Phase B-B
        _phase_b_rows_response([page1_row_a]),
        _phase_b_rows_response([page1_row_b]),
        # Page 2: Phase B-A and Phase B-B with schema A → empty
        _phase_b_rows_response(page2_empty_a),
        _phase_b_rows_response(page2_empty_b),
        # Re-detect Phase A for page 2
        _phase_a_response(_SCHEMA_B),
        # Re-extraction Phase B-A and Phase B-B with schema B
        _phase_b_rows_response([page2_row_a]),
        _phase_b_rows_response([page2_row_b]),
    ]

    llm = _ScriptedLLM(responses)
    images = ["img_page1", "img_page2"]

    rows, _mismatches, _delta, final_columns = await pdf_parser._extract_all_pages_via_vision(
        images, list(_SCHEMA_A), llm
    )

    # Page-1 row must be present (schema A key)
    assert any(r.get("POS") == "1" for r in rows), f"Page-1 row missing: {rows}"

    # Page-2 row must be present (schema B key)
    assert any(r.get("ITEM") == "10" for r in rows), f"Page-2 row missing: {rows}"

    # detected_columns must be extended with schema B names
    for col in _SCHEMA_B:
        assert col in final_columns, f"Column {col!r} not in final_columns: {final_columns}"

    # Re-detect call happened (7 total: 2+2 page1/2 Phase B + 1 Phase A + 2 re-extract)
    assert llm.image_calls == 7, f"Expected 7 LLM calls, got {llm.image_calls}"


# ---------------------------------------------------------------------------
# Test 2: page 2 not anomalous → no second Phase A call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_page2_no_redetect() -> None:
    """BUG-017 negative: page 2 looks normal → no Phase A re-detect call."""
    page_row = {"POS": "1", "BENENNUNG": "PLATE", "STK": "2"}

    responses = [
        # Page 1: Phase B-A and Phase B-B
        _phase_b_rows_response([page_row]),
        _phase_b_rows_response([page_row]),
        # Page 2: Phase B-A and Phase B-B — real rows, no anomaly
        _phase_b_rows_response([{"POS": "2", "BENENNUNG": "CORE", "STK": "1"}]),
        _phase_b_rows_response([{"POS": "2", "BENENNUNG": "CORE", "STK": "1"}]),
    ]

    llm = _ScriptedLLM(responses)
    images = ["img_page1", "img_page2"]

    rows, _mismatches, _delta, final_columns = await pdf_parser._extract_all_pages_via_vision(
        images, list(_SCHEMA_A), llm
    )

    # Exactly 4 calls: no Phase A re-detect.
    assert llm.image_calls == 4, (
        f"Expected 4 LLM calls (no re-detect), got {llm.image_calls}"
    )

    # Both rows present
    positions = [r.get("POS") for r in rows]
    assert "1" in positions
    assert "2" in positions

    # columns unchanged
    assert final_columns == list(_SCHEMA_A)
