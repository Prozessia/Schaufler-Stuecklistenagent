"""A3 acceptance: Vision extraction is always per-page and survives truncation."""

from __future__ import annotations

import pytest

import src.ingestion.pdf_parser as pdf_parser
from src.llm.base import LLMResponse

_COLUMNS = ["POS", "BENENNUNG", "STK"]


def _truncated_rows_json(complete_objects: int) -> str:
    """Return a rows payload with `complete_objects` full objects then a cut-off one."""
    parts = ['{"rows": [']
    for i in range(complete_objects):
        parts.append(f'{{"POS": "{i + 1}", "BENENNUNG": "Teil{i + 1}", "STK": "1"}},')
    # Trailing, deliberately incomplete object → invalid JSON overall (truncation).
    parts.append('{"POS": "999", "BENENNUNG": "Teil')
    return "\n".join(parts)


class _TruncatingLLM:
    """Mock LLM whose image completion always returns truncated JSON."""

    def __init__(self, complete_objects: int) -> None:
        self._content = _truncated_rows_json(complete_objects)
        self.image_calls = 0
        self.batch_calls = 0

    async def complete_with_image(self, **_kwargs: object) -> LLMResponse:
        self.image_calls += 1
        return LLMResponse(
            content=self._content,
            tokens_input=10,
            tokens_output=10,
            model="mock",
            latency_ms=1.0,
        )

    async def complete_with_images(self, **_kwargs: object) -> LLMResponse:
        # Should never be called — the batch path was removed.
        self.batch_calls += 1
        raise AssertionError("batch path must not be used anymore")


@pytest.mark.asyncio
async def test_single_page_path_always_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a 3-page PDF is processed page-by-page (no batch path)."""
    calls: list[tuple[int, str]] = []

    async def _fake_single_page(image, columns, llm, page_num, variant="A"):
        calls.append((page_num, variant))
        return [{"POS": str(page_num), "BENENNUNG": "x", "STK": "1"}]

    monkeypatch.setattr(pdf_parser, "_extract_single_page", _fake_single_page)

    images = ["img1", "img2", "img3"]
    rows, _mismatches = await pdf_parser._extract_all_pages_via_vision(
        images, _COLUMNS, _TruncatingLLM(complete_objects=0)
    )

    # Each of the 3 pages extracted for both dual-extraction variants.
    assert sorted(calls) == [
        (1, "A"), (1, "B"),
        (2, "A"), (2, "B"),
        (3, "A"), (3, "B"),
    ]
    assert len(rows) == 3  # one row per page from variant A


def test_partial_json_recovery() -> None:
    """A truncated payload with 8 complete row objects recovers all 8."""
    raw = _truncated_rows_json(complete_objects=8)

    recovered = pdf_parser._try_recover_partial_json(raw)

    assert len(recovered) == 8
    assert recovered[0] == {"POS": "1", "BENENNUNG": "Teil1", "STK": "1"}
    assert recovered[-1] == {"POS": "8", "BENENNUNG": "Teil8", "STK": "1"}


def test_total_json_failure_returns_empty_not_raises() -> None:
    """Completely invalid JSON → empty list, no exception."""
    recovered = pdf_parser._try_recover_partial_json("this is not json at all <<<")
    assert recovered == []

    rows, parse_ok = pdf_parser._parse_extraction_response(
        "garbage ### no json", _COLUMNS, phase_label="test"
    )
    assert rows == []
    assert parse_ok is False


@pytest.mark.asyncio
async def test_no_totalverlust_on_truncation() -> None:
    """LLM truncates after 5 objects → ≥5 rows recovered, never []."""
    llm = _TruncatingLLM(complete_objects=5)

    rows = await pdf_parser._extract_single_page(
        "img", _COLUMNS, llm, page_num=1, variant="A"
    )

    assert len(rows) >= 5
    assert rows != []
    assert llm.batch_calls == 0  # batch path never used
    assert rows[0]["POS"] == "1"
