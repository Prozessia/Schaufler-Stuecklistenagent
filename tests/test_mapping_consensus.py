"""ARCH-005: independent second opinion on the column mapping.

A confidently wrong primary mapping is the main vector for SYSTEMATIC wrong
GREENs (whole column). The consensus call must agree, otherwise the column's
confidence is capped below the 0.90 green bar.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.core.models import ExtractionMethod, FileFormat, ParsedBOM, SourceMetadata
from src.llm.base import BaseLLM, LLMResponse
from src.mapping.llm_column_mapper import map_columns
from src.mapping.schema_registry import load_schema


class _FakeLLM(BaseLLM):
    """Primary mapping + scripted consensus answer (or failure)."""

    def __init__(self, consensus_assignments=None, consensus_raises=False):
        self.consensus_assignments = consensus_assignments
        self.consensus_raises = consensus_raises
        self.calls: list[dict] = []

    async def complete(self, system, user, *, json_mode=False, temperature=0.0,
                       max_tokens=4096, use_mini=False):
        self.calls.append({"use_mini": use_mini})
        if use_mini:  # consensus call
            if self.consensus_raises:
                raise RuntimeError("azure down")
            content = json.dumps({"assignments": self.consensus_assignments or {}})
        else:  # primary mapping call
            content = json.dumps({
                "mappings": [
                    {
                        "source_column": "POS",
                        "target_field": "Detail Number",
                        "target_column": "A",
                        "confidence": 0.97,
                        "reasoning": "position column",
                    },
                    {
                        "source_column": "WERKST",
                        "target_field": "Material",
                        "target_column": "J",
                        "confidence": 0.95,
                        "reasoning": "material column",
                    },
                ],
                "unmapped_target_fields": [],
            })
        return LLMResponse(content=content, tokens_input=1, tokens_output=1,
                           model="fake", latency_ms=1.0)

    async def complete_with_image(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def complete_with_images(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


def _bom() -> ParsedBOM:
    return ParsedBOM(
        source=SourceMetadata(
            filename="t.pdf", filepath="t.pdf", customer="", format=FileFormat.PDF,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.97,
        ),
        headers=["POS", "WERKST"],
        rows=[{"POS": "1", "WERKST": "1.2343"}],
    )


def _run(llm: _FakeLLM):
    return asyncio.run(map_columns(_bom(), llm, load_schema()))


def test_consensus_agreement_keeps_confidence() -> None:
    llm = _FakeLLM({"POS": "Detail Number", "WERKST": "Material"})
    result = _run(llm)
    pos = result.get_mapping_for_source("POS")
    assert pos.confidence == 0.97
    assert "KONSENS" not in (pos.reasoning or "")


def test_consensus_disagreement_caps_below_green_bar() -> None:
    llm = _FakeLLM({"POS": "Detail Number", "WERKST": "Hardness"})
    result = _run(llm)
    werkst = result.get_mapping_for_source("WERKST")
    assert werkst.confidence <= 0.85
    assert werkst.candidate_confidence <= 0.85
    assert "KONSENS-ABWEICHUNG" in werkst.reasoning
    # the agreeing column is untouched
    assert result.get_mapping_for_source("POS").confidence == 0.97
    assert "Konsens" in result.notes


def test_consensus_null_assignment_counts_as_disagreement() -> None:
    llm = _FakeLLM({"POS": "Detail Number", "WERKST": None})
    result = _run(llm)
    assert result.get_mapping_for_source("WERKST").confidence <= 0.85


def test_consensus_hallucinated_field_is_ignored() -> None:
    """A second opinion naming a non-existent field is no usable evidence."""
    llm = _FakeLLM({"POS": "Detail Number", "WERKST": "Werkstoffgüte XXL"})
    result = _run(llm)
    assert result.get_mapping_for_source("WERKST").confidence == 0.95


def test_consensus_failure_is_fail_open() -> None:
    """An unreachable consensus call must never kill the job or change scores."""
    llm = _FakeLLM(consensus_raises=True)
    result = _run(llm)
    assert result.get_mapping_for_source("WERKST").confidence == 0.95
    assert result.get_mapping_for_source("POS").confidence == 0.97


def test_consensus_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.mapping.llm_column_mapper as mod

    monkeypatch.setattr(mod, "_consensus_enabled", lambda: False)
    llm = _FakeLLM({"POS": "Hardness", "WERKST": "Hardness"})
    result = _run(llm)
    assert result.get_mapping_for_source("POS").confidence == 0.97
    assert all(not c["use_mini"] for c in llm.calls)
