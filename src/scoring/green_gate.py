"""Single source of truth for GREEN classification decisions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.core.models import ExtractionMethod
from src.core.statuses import MatchResult

logger = logging.getLogger(__name__)

_TEXT_PATH_METHODS = {
    "master_data:exact_alias",
    "master_data:exact",
    "master_data:werkstoff_nr_extract",
    "master_data:werkstoff_nr_base",
    # M3: a structurally-valid DIN Werkstoffnummer recognised by format (not in the
    # catalog). Only green-eligible HERE, on the deterministic text path, where the
    # value is read exactly from the text layer — never on a Vision misread.
    "master_data:werkstoff_nr_format",
    "master_data:fuzzy_alias",
    "master_data:fuzzy_material",
    "integer_coerce",
    "decimal_coerce",
    "boolean_normalize",
    "regex_parse",
    "dimension_split",
}

_TEXT_PATH_IGNORED_VETOES = {
    "PDF_COORDINATE_MISMATCH",
    "PDF_COLUMN_CONFLICT",
    "PDF_DUAL_MISMATCH",
}


@dataclass(slots=True)
class GreenGateInput:
    source_is_pdf: bool
    extraction_method: ExtractionMethod | None
    has_text_layer: bool
    vision_fallback_reason: str | None
    green_threshold: float
    verify_green_threshold: float
    soft_green_floor: float
    green_extraction_min_confidence: float
    pdf_extracted_found: bool
    pdf_extraction_confidence: float
    rule_score: float
    value_match_result: MatchResult
    value_match_detail: str
    strict_exact_match: bool
    field_category: str
    check2_reason: str = ""
    candidate_confidence: float = 0.0
    transform_method: str = ""
    transform_confidence: float = 0.0
    counter_check_required: bool = False
    counter_check_passed: bool = False
    blocking_errors: list[str] = field(default_factory=list)
    hard_vetoes: list[str] = field(default_factory=list)
    # C2: plausibility of the cell value (set by the scorer). On the Vision
    # verified-scan path (no text layer), an implausible value (e.g. qty=70000,
    # empty designation) must not be promoted to GREEN even when both dual
    # extractions agree. Defaults True so other paths are never false-negatived.
    value_plausible: bool = True
    # Optional context for debug logging only.
    plausibility_field: str = ""
    plausibility_value: str = ""


def effective_hard_vetoes_for(gate_input: GreenGateInput) -> list[str]:
    return _effective_hard_vetoes(gate_input)


def text_path_transform_verified(gate_input: GreenGateInput) -> bool:
    return _text_path_method_verified(gate_input)


def can_be_green(gate_input: GreenGateInput) -> tuple[bool, list[str]]:
    """Return (is_green, evidence). This is the only GREEN gate in the system."""
    evidence: list[str] = []
    is_text_path = gate_input.extraction_method == ExtractionMethod.PYMUPDF_TEXT
    effective_hard_vetoes = effective_hard_vetoes_for(gate_input)

    if gate_input.blocking_errors:
        return False, ["BLOCKING_VALIDATOR_ERROR"]

    if effective_hard_vetoes:
        return False, ["HARD_VETO_PRESENT"]

    if not gate_input.source_is_pdf:
        return False, ["NO_PDF_EVIDENCE"]

    if gate_input.vision_fallback_reason:
        return False, ["VISION_FALLBACK_TO_LEGACY_PARSER"]

    if is_text_path:
        return _evaluate_text_path(gate_input)

    verified_scan = _is_verified_scan(gate_input, effective_hard_vetoes)

    if not gate_input.has_text_layer and not verified_scan:
        return False, _with_check2_reason(
            ["NO_PDF_TEXT_LAYER_UNVERIFIED_SCAN"],
            gate_input,
        )

    if not gate_input.pdf_extracted_found and not verified_scan:
        return False, _with_check2_reason(["CHECK2_EXTRACTION_MISSING"], gate_input)

    if (
        gate_input.pdf_extraction_confidence
        < gate_input.green_extraction_min_confidence
        and not verified_scan
    ):
        return False, _with_check2_reason(
            ["CHECK2_EXTRACTION_LOW_CONFIDENCE"],
            gate_input,
        )

    if gate_input.rule_score < gate_input.verify_green_threshold:
        return False, ["CHECK4_RULE_SCORE_BELOW_VERIFY_THRESHOLD"]

    if gate_input.value_match_result != MatchResult.MATCH and not verified_scan:
        return False, ["CHECK3_NOT_MATCH"]

    if gate_input.counter_check_required and not gate_input.counter_check_passed:
        return False, ["CHECK5_COUNTER_CHECK_FAILED"]

    if (
        gate_input.field_category == "A"
        and not gate_input.strict_exact_match
        and not verified_scan
    ):
        return False, ["CATEGORY_A_REQUIRES_EXACT_MATCH"]

    evidence.extend(
        _with_check2_reason(
            [
                (
                    "CHECK3_MATCH"
                    if gate_input.value_match_result == MatchResult.MATCH
                    else "VERIFIED_SCAN"
                ),
                "NO_BLOCKING_VALIDATOR_ERROR",
                "NO_HARD_VETO",
                f"CHECK4_RULE_GE_{gate_input.verify_green_threshold:.2f}",
                f"CHECK2_CONF_{gate_input.pdf_extraction_confidence:.2f}",
            ],
            gate_input,
        )
    )

    if gate_input.field_category == "A" and gate_input.strict_exact_match:
        evidence.append("CATEGORY_A_EXACT_MATCH")

    if gate_input.counter_check_required:
        evidence.append("CHECK5_COUNTER_CHECK_PASS")

    if verified_scan:
        evidence.append("SCAN_VERIFIED_WITHOUT_TEXT_LAYER")

    return True, evidence


def _evaluate_text_path(gate_input: GreenGateInput) -> tuple[bool, list[str]]:
    if not gate_input.has_text_layer:
        return False, _with_check2_reason(["TEXT_PATH_REQUIRES_TEXT_LAYER"], gate_input)

    if not gate_input.pdf_extracted_found:
        return False, _with_check2_reason(["CHECK2_EXTRACTION_MISSING"], gate_input)

    if (
        gate_input.pdf_extraction_confidence
        < gate_input.green_extraction_min_confidence
    ):
        return False, _with_check2_reason(
            ["CHECK2_EXTRACTION_LOW_CONFIDENCE"],
            gate_input,
        )

    if gate_input.candidate_confidence < 0.90:
        return False, ["TEXT_PATH_MAPPING_CONFIDENCE_LOW"]

    if gate_input.rule_score < gate_input.verify_green_threshold:
        return False, ["TEXT_PATH_RULE_SCORE_BELOW_VERIFY_THRESHOLD"]

    if gate_input.value_match_result == MatchResult.MISMATCH:
        return False, ["CHECK3_VALUE_MISMATCH"]

    if (
        gate_input.field_category == "A"
        and gate_input.value_match_result == MatchResult.MATCH
    ):
        if not gate_input.strict_exact_match:
            return False, ["CATEGORY_A_REQUIRES_EXACT_MATCH"]

    method_verified = _text_path_method_verified(gate_input)
    if not method_verified:
        return False, ["TEXT_PATH_TRANSFORM_NOT_VERIFIED"]

    evidence = _with_check2_reason(
        [
            "TEXT_PATH_HIGH_MAPPING_CONFIDENCE",
            f"TEXT_PATH_RULE_GE_{gate_input.verify_green_threshold:.2f}",
            f"CHECK2_CONF_{gate_input.pdf_extraction_confidence:.2f}",
            f"TEXT_PATH_METHOD={gate_input.transform_method or 'unknown'}",
        ],
        gate_input,
    )

    if gate_input.transform_method.startswith("master_data:"):
        evidence.append("MASTER_DATA_MATCH_CONFIRMED")

    if gate_input.value_match_result == MatchResult.MATCH:
        evidence.append("CHECK3_MATCH")
    elif gate_input.value_match_result == MatchResult.UNCERTAIN:
        evidence.append("CHECK3_OPTIONAL_FOR_TEXT_PATH")

    return True, evidence


def _effective_hard_vetoes(gate_input: GreenGateInput) -> list[str]:
    if gate_input.extraction_method != ExtractionMethod.PYMUPDF_TEXT:
        return list(gate_input.hard_vetoes)

    return [
        veto for veto in gate_input.hard_vetoes if veto not in _TEXT_PATH_IGNORED_VETOES
    ]


def _text_path_method_verified(gate_input: GreenGateInput) -> bool:
    if gate_input.transform_method in _TEXT_PATH_METHODS:
        return True
    if gate_input.transform_confidence >= 0.95:
        return True
    return False


def _with_check2_reason(evidence: list[str], gate_input: GreenGateInput) -> list[str]:
    if gate_input.check2_reason:
        return [*evidence, f"CHECK2_REASON_{gate_input.check2_reason}"]
    return evidence


def _is_verified_scan(
    gate_input: GreenGateInput,
    effective_hard_vetoes: list[str],
) -> bool:
    if gate_input.extraction_method != ExtractionMethod.GPT4O_VISION:
        return False
    if gate_input.has_text_layer:
        return False
    if gate_input.vision_fallback_reason:
        return False
    if gate_input.blocking_errors:
        return False
    if effective_hard_vetoes:
        return False
    if gate_input.rule_score < gate_input.verify_green_threshold:
        return False
    if not gate_input.counter_check_required:
        return False
    if not gate_input.counter_check_passed:
        return False
    # C2: even with dual-extraction + counter-check agreement, block GREEN when
    # the value itself is implausible (two LLM passes making the same misread).
    if not gate_input.value_plausible:
        logger.debug(
            "GREEN blocked: verified_scan=True but value_plausible=False, "
            "field=%s, value=%s",
            gate_input.plausibility_field,
            gate_input.plausibility_value,
        )
        return False
    return True
