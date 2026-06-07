"""C2 acceptance: implausible values can't earn GREEN on the Vision scan path."""

from __future__ import annotations

from src.core.models import ExtractionMethod
from src.core.statuses import MatchResult
from src.scoring.ensemble_scorer import _is_value_plausible
from src.scoring.green_gate import GreenGateInput, can_be_green


def _verified_scan_gate(
    *, field: str, value: str, value_plausible: bool | None = None
) -> GreenGateInput:
    """Build a GreenGateInput that would be GREEN via the Vision verified-scan path.

    No text layer, dual-extraction + counter-check passed, no vetoes. The only
    variable under test is value plausibility.
    """
    if value_plausible is None:
        value_plausible = _is_value_plausible(field, value)
    return GreenGateInput(
        source_is_pdf=True,
        extraction_method=ExtractionMethod.GPT4O_VISION,
        has_text_layer=False,
        vision_fallback_reason=None,
        green_threshold=0.90,
        verify_green_threshold=0.60,
        soft_green_floor=0.50,
        green_extraction_min_confidence=0.70,
        pdf_extracted_found=False,
        pdf_extraction_confidence=0.0,
        rule_score=0.95,
        value_match_result=MatchResult.UNCERTAIN,
        value_match_detail="",
        strict_exact_match=False,
        field_category="A",
        counter_check_required=True,
        counter_check_passed=True,
        value_plausible=value_plausible,
        plausibility_field=field,
        plausibility_value=value,
    )


def test_no_false_green_implausible_qty() -> None:
    gate = _verified_scan_gate(field="Design Count", value="70000")
    is_green, evidence = can_be_green(gate)
    assert is_green is False
    assert "NO_PDF_TEXT_LAYER_UNVERIFIED_SCAN" in evidence


def test_no_false_green_empty_designation() -> None:
    gate = _verified_scan_gate(field="Description", value="")
    is_green, _ = can_be_green(gate)
    assert is_green is False


def test_green_still_possible_plausible_values() -> None:
    # qty=5 and a real designation are plausible → verified-scan GREEN stays.
    qty_gate = _verified_scan_gate(field="Design Count", value="5")
    desc_gate = _verified_scan_gate(field="Description", value="Passbolzen")

    qty_green, qty_ev = can_be_green(qty_gate)
    desc_green, _ = can_be_green(desc_gate)

    assert qty_green is True
    assert "VERIFIED_SCAN" in qty_ev or "SCAN_VERIFIED_WITHOUT_TEXT_LAYER" in qty_ev
    assert desc_green is True


def test_no_qty_field_does_not_block_green() -> None:
    # A non-quantity, non-description field is always plausible → no block.
    assert _is_value_plausible("Detail Number", "1-1") is True
    gate = _verified_scan_gate(field="Detail Number", value="1-1")
    is_green, _ = can_be_green(gate)
    assert is_green is True
