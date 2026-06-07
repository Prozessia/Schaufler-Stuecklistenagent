"""M3 contract: a format-recognised Werkstoffnummer is GREEN-eligible on the
deterministic text path, and NOT on the Vision path (no exact-read guarantee)."""

from __future__ import annotations

from src.core.models import ExtractionMethod
from src.core.statuses import MatchResult
from src.scoring.ensemble_scorer import _method_quality_score
from src.scoring.green_gate import GreenGateInput, can_be_green


def _text_path_gate(**override: object) -> GreenGateInput:
    base: dict[str, object] = dict(
        source_is_pdf=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
        has_text_layer=True,
        vision_fallback_reason=None,
        green_threshold=0.90,
        verify_green_threshold=0.90,
        soft_green_floor=0.70,
        green_extraction_min_confidence=0.70,
        pdf_extracted_found=True,
        pdf_extraction_confidence=0.97,
        rule_score=0.975,  # 0.30 + 0.30 + 0.25*0.90 + 0.15
        value_match_result=MatchResult.MATCH,
        value_match_detail="exact normalized match",
        strict_exact_match=False,
        field_category="C",  # Material is non-required → not strict category A
        candidate_confidence=0.97,
        transform_method="master_data:werkstoff_nr_format",
        transform_confidence=0.92,
    )
    base.update(override)
    return GreenGateInput(**base)  # type: ignore[arg-type]


def test_method_quality_makes_rule_score_green_capable() -> None:
    """Quality must keep rule_score >= verify_green_threshold (0.90)."""
    quality = _method_quality_score("master_data:werkstoff_nr_format")
    rule_score = 0.30 + 0.30 + 0.25 * quality + 0.15
    assert rule_score >= 0.90


def test_werkstoff_nr_format_is_green_on_text_path() -> None:
    is_green, _ = can_be_green(_text_path_gate())
    assert is_green is True


def test_werkstoff_nr_format_not_green_on_vision_path() -> None:
    """Same value on the Vision path (no text layer) must NOT be green — a Vision
    misread of a number is possible; only the exact-read text path may certify."""
    is_green, evidence = can_be_green(
        _text_path_gate(
            extraction_method=ExtractionMethod.GPT4O_VISION,
            has_text_layer=False,
            pdf_extracted_found=False,
            pdf_extraction_confidence=0.0,
        )
    )
    assert is_green is False
