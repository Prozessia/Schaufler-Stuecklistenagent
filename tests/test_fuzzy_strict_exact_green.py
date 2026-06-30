"""GREEN-RECOVERY P2 contract: a fuzzy master-data hit is GREEN-eligible on the
text path ONLY when the comparator independently re-confirms the value with a
STRICT-EXACT match (e.g. the unique DIN Werkstoffnummer). A fuzzy hit that is
merely a semantic-alias equivalence (strict_exact=False) stays non-verified —
guarding the wrong-canonical collision case (BUG-008)."""

from __future__ import annotations

from src.core.models import ExtractionMethod
from src.core.statuses import MatchResult
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
        pdf_extraction_confidence=0.95,
        rule_score=0.988,  # fuzzy_alias quality keeps rule_score >= 0.90
        value_match_result=MatchResult.MATCH,
        value_match_detail="same werkstoffnummer (catalog variant)",
        strict_exact_match=True,
        field_category="A",  # Material is category A
        candidate_confidence=0.95,
        transform_method="master_data:fuzzy_alias",
        transform_confidence=0.95,
    )
    base.update(override)
    return GreenGateInput(**base)  # type: ignore[arg-type]


def test_fuzzy_hit_with_strict_exact_match_is_green() -> None:
    """Fuzzy material hit re-confirmed via the unique Werkstoffnummer → GREEN."""
    is_green, evidence = can_be_green(_text_path_gate())
    assert is_green is True
    assert "MASTER_DATA_MATCH_CONFIRMED" in evidence


def test_fuzzy_hit_without_strict_exact_stays_non_green() -> None:
    """A fuzzy hit that is only a semantic-alias equivalence (strict_exact=False)
    — the wrong-canonical collision BUG-008 guards against — stays non-verified.
    Category A additionally requires exact identity, so this is doubly blocked."""
    is_green, evidence = can_be_green(
        _text_path_gate(
            strict_exact_match=False,
            value_match_detail="semantic equivalent via alias canonicalization",
        )
    )
    assert is_green is False


def test_fuzzy_hit_strict_exact_category_c_is_green() -> None:
    """The strict-exact verification applies to any method, not just category A."""
    is_green, _ = can_be_green(_text_path_gate(field_category="C"))
    assert is_green is True
