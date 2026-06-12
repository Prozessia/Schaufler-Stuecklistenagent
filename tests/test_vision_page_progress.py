"""PERF-001 (2c + 2d): Page progress callback and VISION_PAGE_CONCURRENCY test.

Uses the same _ScriptedLLM pattern as test_vision_page_structure.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.ingestion.pdf_parser as pdf_parser
from src.llm.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers (mirrors test_vision_page_structure.py)
# ---------------------------------------------------------------------------


def _llm_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        tokens_input=10,
        tokens_output=10,
        model="mock",
        latency_ms=1.0,
    )


def _phase_b_rows_response(rows: list[dict]) -> str:
    return json.dumps({"rows": rows})


class _ScriptedLLM:
    """Mock LLM that serves pre-configured responses in sequence."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.image_calls: int = 0

    async def complete_with_image(self, **kwargs: object) -> LLMResponse:
        self.image_calls += 1
        if not self._responses:
            return _llm_response(_phase_b_rows_response([]))
        return _llm_response(self._responses.pop(0))


# ---------------------------------------------------------------------------
# Schema used throughout
# ---------------------------------------------------------------------------

_SCHEMA = ["POS", "BENENNUNG", "STK"]

_PAGE1_ROW = {"POS": "1", "BENENNUNG": "Formplatte", "STK": "2"}
_PAGE2_ROW = {"POS": "2", "BENENNUNG": "Schieber",   "STK": "4"}


# ---------------------------------------------------------------------------
# Test 1: progress_callback is called once per page with correct (done, total)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_callback_called_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """progress_callback receives (1, 2) then (2, 2) for a 2-page document."""
    monkeypatch.setenv("VISION_PAGE_CONCURRENCY", "1")

    responses = [
        # Page 1: Phase B-A and Phase B-B
        _phase_b_rows_response([_PAGE1_ROW]),
        _phase_b_rows_response([_PAGE1_ROW]),
        # Page 2: Phase B-A and Phase B-B
        _phase_b_rows_response([_PAGE2_ROW]),
        _phase_b_rows_response([_PAGE2_ROW]),
    ]

    llm = _ScriptedLLM(responses)
    images = ["img_page1_b64", "img_page2_b64"]

    calls: list[tuple[int, int]] = []

    def _cb(done: int, total: int) -> None:
        calls.append((done, total))

    rows, _mismatches, _delta, _cols = await pdf_parser._extract_all_pages_via_vision(
        images, list(_SCHEMA), llm, progress_callback=_cb
    )

    assert calls == [(1, 2), (2, 2)], f"Unexpected callback sequence: {calls}"
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# Test 2: VISION_PAGE_CONCURRENCY=2 produces same rows as serial (order stable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_2_produces_same_rows_as_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With VISION_PAGE_CONCURRENCY=2 the output rows match the serial result."""
    # Serial run (concurrency=1)
    monkeypatch.setenv("VISION_PAGE_CONCURRENCY", "1")

    responses_serial = [
        _phase_b_rows_response([_PAGE1_ROW]),
        _phase_b_rows_response([_PAGE1_ROW]),
        _phase_b_rows_response([_PAGE2_ROW]),
        _phase_b_rows_response([_PAGE2_ROW]),
    ]
    llm_serial = _ScriptedLLM(responses_serial)
    images = ["img_p1", "img_p2"]

    serial_rows, _, _, _ = await pdf_parser._extract_all_pages_via_vision(
        images, list(_SCHEMA), llm_serial
    )

    # Parallel run (concurrency=2)
    monkeypatch.setenv("VISION_PAGE_CONCURRENCY", "2")

    responses_parallel = [
        _phase_b_rows_response([_PAGE1_ROW]),
        _phase_b_rows_response([_PAGE1_ROW]),
        _phase_b_rows_response([_PAGE2_ROW]),
        _phase_b_rows_response([_PAGE2_ROW]),
    ]
    llm_parallel = _ScriptedLLM(responses_parallel)

    parallel_rows, _, _, _ = await pdf_parser._extract_all_pages_via_vision(
        images, list(_SCHEMA), llm_parallel
    )

    assert serial_rows == parallel_rows, (
        f"Serial rows: {serial_rows}\nParallel rows: {parallel_rows}"
    )


# ---------------------------------------------------------------------------
# Test 3: exception in progress_callback does not abort the pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_callback_exception_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception raised inside progress_callback must not break extraction."""
    monkeypatch.setenv("VISION_PAGE_CONCURRENCY", "1")

    responses = [
        _phase_b_rows_response([_PAGE1_ROW]),
        _phase_b_rows_response([_PAGE1_ROW]),
    ]
    llm = _ScriptedLLM(responses)
    images = ["img_page1_b64"]

    def _bad_cb(done: int, total: int) -> None:
        raise RuntimeError("callback exploded!")

    # Should not raise
    rows, _, _, _ = await pdf_parser._extract_all_pages_via_vision(
        images, list(_SCHEMA), llm, progress_callback=_bad_cb
    )

    assert len(rows) == 1
