"""A column-level MAPPING_VALIDATOR_ERROR must not force a valued cell to RED.

Background: the mapping validator flags whole TARGET FIELDS (columns) on errors
like duplicate assignment or type mismatch. Previously every cell in such a
column was forced RED — even a cleanly extracted, value-matched cell (this is
why "Design Count" came out 114/114 RED on a real export). A column-level
complaint should *withhold GREEN* (cap to YELLOW for review), not destroy a
cell that carries a usable value.

Guarantee preserved: a flagged cell can still NEVER become GREEN.
"""

from __future__ import annotations

from src.core.models import (
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceMetadata,
)
from src.core.statuses import MatchResult
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.mapping_validator import ValidationIssue, ValidationResult
from src.scoring.ensemble_scorer import score_bom
from src.scoring.green_gate import GreenGateInput, can_be_green
from src.scoring.threshold_manager import TrafficLight
from src.transform.pipeline import transform_bom


def _green_ready_gate_input(**overrides) -> GreenGateInput:
    """A gate input that WOULD be green, so a single blocker is the only thing
    that can flip it. Used to prove the blocker still wins."""
    base = dict(
        source_is_pdf=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
        has_text_layer=True,
        vision_fallback_reason=None,
        green_threshold=0.90,
        verify_green_threshold=0.90,
        soft_green_floor=0.70,
        green_extraction_min_confidence=0.70,
        pdf_extracted_found=True,
        pdf_extraction_confidence=0.99,
        rule_score=0.99,
        value_match_result=MatchResult.MATCH,
        value_match_detail="exact normalized match",
        strict_exact_match=True,
        field_category="B",
        candidate_confidence=0.99,
        transform_method="integer_coerce",
        transform_confidence=0.99,
    )
    base.update(overrides)
    return GreenGateInput(**base)


def test_blocking_validator_error_can_never_be_green():
    """GUARANTEE: an otherwise-green cell with a MAPPING_VALIDATOR_ERROR stays
    non-green. This invariant must hold before AND after the RED->YELLOW change."""
    gi = _green_ready_gate_input(blocking_errors=["MAPPING_VALIDATOR_ERROR"])
    is_green, evidence = can_be_green(gi)
    assert is_green is False
    assert "BLOCKING_VALIDATOR_ERROR" in evidence

    # control: without the blocker the very same input IS green (proves the
    # blocker is the deciding factor, not some other unmet condition).
    is_green_clean, _ = can_be_green(_green_ready_gate_input())
    assert is_green_clean is True


def _bom() -> ParsedBOM:
    rows = [
        {"Pos": "10", "Name": "Kern", "Mat": "1.2343"},
        {"Pos": "20", "Name": "Schieber", "Mat": "1.2344"},
    ]
    return ParsedBOM(
        source=SourceMetadata(
            filepath="demo.xlsx", filename="demo.xlsx", format=FileFormat.EXCEL
        ),
        headers=["Pos", "Name", "Mat"],
        rows=rows,
    )


def _mapping() -> MappingResult:
    return MappingResult(
        mappings=[
            ColumnMapping(
                source_column="Pos", target_field="Detail Number",
                confidence=0.95, candidate_confidence=0.95,
            ),
            ColumnMapping(
                source_column="Name", target_field="Description",
                confidence=0.95, candidate_confidence=0.95,
            ),
            ColumnMapping(
                source_column="Mat", target_field="Material",
                confidence=0.95, candidate_confidence=0.95,
            ),
        ]
    )


def test_valued_cell_in_flagged_column_is_yellow_not_red():
    """BEHAVIOUR: a clean Material value in a column the validator flagged with
    an ERROR is YELLOW (review), not RED."""
    bom = _bom()
    mapping = _mapping()
    transform_result = transform_bom(bom, mapping)
    validation = ValidationResult(
        issues=[
            ValidationIssue(
                severity="error",
                message="simulated column-level mapping error",
                target_field="Material",
            )
        ]
    )

    audit = score_bom(transform_result, mapping, mapping_validation=validation)

    material_cells = [
        c
        for c in audit.cells
        if c.target_field == "Material" and (c.transformed_value or "").strip()
    ]
    assert material_cells, "expected non-empty Material cells in the audit"
    for cell in material_cells:
        assert cell.classification == TrafficLight.YELLOW, (
            f"{cell.transformed_value!r} -> {cell.classification} "
            f"(reason: {cell.reasoning})"
        )
        assert cell.classification != TrafficLight.GREEN
