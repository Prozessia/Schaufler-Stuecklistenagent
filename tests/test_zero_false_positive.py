from __future__ import annotations

from pathlib import Path

from src.api.cell_edits import apply_cell_edits
from src.api.models.schemas import CellEditRequest
from src.core.models import (
    CellTransformation,
    ExtractionMethod,
    FileFormat,
    ParsedBOM,
    SourceLocation,
    SourceMetadata,
    TransformationResult,
    TransformedRow,
)
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.mapping_validator import ValidationIssue, ValidationResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.reconciliation.position_reconciler import reconcile_positions
from src.scoring.ensemble_scorer import (
    _extract_pdf_positions_from_pages,
    _extract_pdf_positions_from_document_text,
    score_bom,
)
from src.scoring.threshold_manager import ScoringConfig, TrafficLight
from src.scoring.value_comparator import ValueComparator
from src.scoring.vision_verifier import CounterCheckResult
from src.core.statuses import MatchResult
from src.transform.cross_validator import CrossValidationResult
from src.transform.pipeline import transform_bom


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            TargetField(
                name="Design Count",
                name_de="Stk. Konstr.",
                column="D",
                type="integer",
                required=True,
            )
        ]
    )


def _mapping(confidence: float = 0.99) -> MappingResult:
    return MappingResult(
        source_file="sample",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Qty",
                target_field="Design Count",
                target_column="D",
                confidence=confidence,
                reasoning="Qty clearly maps to Design Count",
                candidate_confidence=confidence,
                candidate_reasoning="Qty clearly maps to Design Count",
            )
        ],
    )


def _bom(
    *,
    fmt: FileFormat,
    value: str = "12",
    extracted: str = "12",
    has_text_layer: bool = True,
    row_validation_flags: dict[int, list[str]] | None = None,
    vision_fallback_reason: str | None = None,
    extraction_method: ExtractionMethod | None = None,
    extraction_confidence: float = 0.95,
    source_match_type: str = "column_corridor",
    llm_json_repaired: bool = False,
    llm_uncertain_cells: dict[int, list[str]] | None = None,
    document_text_layer: str = "",
) -> ParsedBOM:
    metadata = {
        "has_text_layer": has_text_layer,
        "document_text_layer": document_text_layer,
        "source_locations": {
            0: {
                "Qty": {
                    "page": 1,
                    "bbox": [10.0, 20.0, 30.0, 40.0],
                    "text": extracted,
                    "match_type": source_match_type,
                }
            }
        },
    }
    if row_validation_flags:
        metadata["row_validation_flags"] = row_validation_flags
    if vision_fallback_reason:
        metadata["vision_fallback_reason"] = vision_fallback_reason
    if llm_json_repaired:
        metadata["llm_json_repaired"] = True
    if llm_uncertain_cells:
        metadata["llm_uncertain_cells"] = llm_uncertain_cells

    return ParsedBOM(
        source=SourceMetadata(
            filename="sample.pdf" if fmt == FileFormat.PDF else "sample.xlsx",
            filepath="sample.pdf" if fmt == FileFormat.PDF else "sample.xlsx",
            customer="ACME",
            format=fmt,
            extraction_method=extraction_method,
            extraction_confidence=extraction_confidence,
        ),
        headers=["Qty"],
        rows=[{"Qty": value}],
        metadata=metadata,
    )


def _score(
    bom: ParsedBOM,
    *,
    mapping: MappingResult | None = None,
    cv_result: CrossValidationResult | None = None,
    config: ScoringConfig | None = None,
    mapping_validation: ValidationResult | None = None,
    counter_check_service=None,
    job_id: str | None = None,
    pdf_path: Path | None = None,
):
    schema = _schema()
    mapping = mapping or _mapping()
    transformed = transform_bom(bom, mapping, schema=schema)
    return score_bom(
        transformed,
        mapping,
        cv_result=cv_result,
        schema=schema,
        config=config,
        mapping_validation=mapping_validation,
        counter_check_service=counter_check_service,
        job_id=job_id,
        pdf_path=pdf_path,
    )


def _counter_check_config() -> ScoringConfig:
    return ScoringConfig(enable_counter_check=True)


class _StubCounterCheckService:
    def __init__(self, *, passed: bool, observed_value: str = "12") -> None:
        self._passed = passed
        self._observed_value = observed_value
        self.calls = 0

    async def verify(self, request):
        self.calls += 1
        return CounterCheckResult(
            passed=self._passed,
            score=1.0 if self._passed else 0.0,
            reason="stub",
            notes=(
                f"stub_primary={request.primary_extracted_value}; "
                f"stub_secondary={self._observed_value}"
            ),
            secondary_value=self._observed_value,
            secondary_confidence=0.99,
        )

    async def verify_fields(self, job_id, pdf_path, page_number, requests):
        """PERF-002: one batched call per page — `calls` counts CALLS, not cells."""
        self.calls += 1
        return {
            r.request_id: CounterCheckResult(
                passed=self._passed,
                score=1.0 if self._passed else 0.0,
                reason="stub",
                notes=(
                    f"stub_primary={r.primary_value}; "
                    f"stub_secondary={self._observed_value}"
                ),
                secondary_value=self._observed_value,
                secondary_confidence=0.99,
            )
            for r in requests
        }


def _schema_with_detail_customer_and_design_count() -> TargetSchema:
    return TargetSchema(
        fields=[
            TargetField(
                name="Detail Number",
                name_de="Positionsnummer",
                column="A",
                type="string",
                required=True,
            ),
            TargetField(
                name="Customer Part Number",
                name_de="Kunden-Teilenummer",
                column="B",
                type="string",
                required=False,
            ),
            TargetField(
                name="Design Count",
                name_de="Stk. Konstr.",
                column="C",
                type="integer",
                required=True,
            ),
            TargetField(
                name="Description",
                name_de="Benennung",
                column="D",
                type="string",
                required=False,
            ),
        ]
    )


def _mapping_with_detail_customer_and_design_count() -> MappingResult:
    return MappingResult(
        source_file="sample.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Pos",
                target_field="Detail Number",
                target_column="A",
                confidence=0.95,
                reasoning="Pos maps to Detail Number",
                candidate_confidence=0.95,
                candidate_reasoning="Pos maps to Detail Number",
            ),
            ColumnMapping(
                source_column="Part",
                target_field="Customer Part Number",
                target_column="B",
                confidence=0.95,
                reasoning="Part maps to Customer Part Number",
                candidate_confidence=0.95,
                candidate_reasoning="Part maps to Customer Part Number",
            ),
            ColumnMapping(
                source_column="Qty",
                target_field="Design Count",
                target_column="C",
                confidence=0.95,
                reasoning="Qty maps to Design Count",
                candidate_confidence=0.95,
                candidate_reasoning="Qty maps to Design Count",
            ),
            ColumnMapping(
                source_column="Desc",
                target_field="Description",
                target_column="D",
                confidence=0.95,
                reasoning="Desc maps to Description",
                candidate_confidence=0.95,
                candidate_reasoning="Desc maps to Description",
            ),
        ],
    )


def test_non_pdf_is_never_green_even_with_perfect_values() -> None:
    audit = _score(_bom(fmt=FileFormat.EXCEL, value="12", extracted="12"))
    assert audit.cells[0].classification == TrafficLight.YELLOW


def test_pdf_without_text_layer_is_never_green() -> None:
    audit = _score(_bom(fmt=FileFormat.PDF, has_text_layer=False))
    assert audit.cells[0].classification == TrafficLight.YELLOW


def test_pdf_only_positions_are_added_to_audit_trail_as_missing_erp_rows() -> None:
    schema = _schema_with_detail_customer_and_design_count()
    mapping = _mapping_with_detail_customer_and_design_count()
    transform_result = TransformationResult(
        source_file="sample.pdf",
        customer="ACME",
        rows=[
            TransformedRow(
                row_index=0,
                cells=[
                    CellTransformation(
                        target_field="Detail Number",
                        target_column="A",
                        source_column="Pos",
                        raw_value="1-01",
                        transformed_value="1-01",
                        confidence=0.95,
                        method="passthrough",
                    ),
                    CellTransformation(
                        target_field="Customer Part Number",
                        target_column="B",
                        source_column="Part",
                        raw_value="ABC-111",
                        transformed_value="ABC-111",
                        confidence=0.95,
                        method="passthrough",
                    ),
                    CellTransformation(
                        target_field="Design Count",
                        target_column="C",
                        source_column="Qty",
                        raw_value="2",
                        transformed_value="2",
                        confidence=0.95,
                        method="passthrough",
                    ),
                    CellTransformation(
                        target_field="Description",
                        target_column="D",
                        source_column="Desc",
                        raw_value="ANGLE PLATE",
                        transformed_value="ANGLE PLATE",
                        confidence=0.95,
                        method="passthrough",
                    ),
                ],
            )
        ],
        source_is_pdf=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
        source_extraction_confidence=0.95,
        has_text_layer=True,
    )

    # B2/B3: the PDF carries an extra position (K-228) absent from the extracted
    # rows. The reconciler injects it as a synthetic MISSING row; the scorer
    # hard-vetoes it to RED. (Replaces the removed _append_pdf_only_position_audits
    # 4-field PDF reconstruction — a MISSING position is now position-only.)
    reconcile_positions(transform_result, ["1-01", "K-228"], schema)

    audit = score_bom(transform_result, mapping, schema=schema)

    missing_cells = [
        cell for cell in audit.cells if "RECONCILER_MISSING_POSITION" in cell.hard_vetoes
    ]
    assert len(missing_cells) == 1

    detail_cell = missing_cells[0]
    assert detail_cell.target_field == "Detail Number"
    assert detail_cell.transformed_value == "K-228"
    assert detail_cell.raw_value is None
    assert detail_cell.classification == TrafficLight.RED
    assert (
        detail_cell.reasoning
        == "Critical Error: Position exists on PDF drawing/part list, but is completely missing in ERP master data (Excel)"
    )


def test_pdf_position_extractor_finds_positions_not_at_line_start() -> None:
    positions = _extract_pdf_positions_from_document_text(
        "67 2-040 ANGLE PLATE 1 STK\nnoise K-228 BUY PART 2 STK"
    )

    assert [position.position for position in positions] == ["2-040", "K-228"]


def test_pdf_position_extractor_scans_pages_independently() -> None:
    positions = _extract_pdf_positions_from_pages(
        [
            "1-01 ANGLE PLATE 2 STK\n1-02 CORE 1 STK",
            "67 2-040 SLIDER 1 STK\nnoise K-228 BUY PART 2 STK",
        ]
    )

    assert [position.position for position in positions] == [
        "1-01",
        "1-02",
        "2-040",
        "K-228",
    ]
    assert [position.page_index for position in positions] == [0, 0, 1, 1]


def test_text_path_can_be_green_without_image_world_vetoes() -> None:
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            has_text_layer=True,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            row_validation_flags={
                0: ["COORDMISS:Qty: mismatch", "COORDCOL:Qty: conflict"]
            },
        )
    )

    assert audit.cells[0].classification == TrafficLight.GREEN
    assert "TEXT_PATH_HIGH_MAPPING_CONFIDENCE" in audit.cells[0].green_evidence


def test_text_path_check2_uses_fixed_digital_confidence_not_match_type_heuristic() -> (
    None
):
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            has_text_layer=True,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.95,
            source_match_type="row_fallback",
        )
    )

    assert audit.cells[0].classification == TrafficLight.GREEN
    assert audit.cells[0].pdf_extraction_confidence == 0.95
    assert audit.cells[0].check2_reason == "text_layer_direct"
    assert "CHECK2_CONF_0.95" in audit.cells[0].green_evidence
    assert "CHECK2_REASON_text_layer_direct" in audit.cells[0].green_evidence


def test_text_path_json_repair_lowers_check2_confidence_without_using_vision_heuristics() -> (
    None
):
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            has_text_layer=True,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.95,
            source_match_type="row_fallback",
            llm_json_repaired=True,
        )
    )

    assert audit.cells[0].pdf_extraction_confidence == 0.86
    assert audit.cells[0].check2_reason == "json_repaired"
    assert audit.cells[0].classification == TrafficLight.GREEN
    assert "CHECK2_REASON_json_repaired" in audit.cells[0].green_evidence


def test_text_path_explicit_uncertain_field_lowers_check2_and_blocks_green() -> None:
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            has_text_layer=True,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.95,
            llm_uncertain_cells={0: ["Qty"]},
        )
    )

    assert audit.cells[0].pdf_extraction_confidence == 0.68
    assert audit.cells[0].check2_reason == "llm_uncertain_field"
    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert "CHECK2_EXTRACTION_LOW_CONFIDENCE" in audit.cells[0].reasoning
    assert "CHECK2_REASON_llm_uncertain_field" in audit.cells[0].reasoning


def test_unverified_scan_without_text_layer_stays_non_green() -> None:
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="",
            has_text_layer=False,
            extraction_method=ExtractionMethod.GPT4O_VISION,
        )
    )

    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert "NO_PDF_TEXT_LAYER_UNVERIFIED_SCAN" in audit.cells[0].reasoning


def test_text_path_image_flags_do_not_force_red_or_zero_verify_score() -> None:
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            has_text_layer=True,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            row_validation_flags={
                0: ["COORDMISS:Qty: mismatch", "COORDCOL:Qty: conflict"]
            },
        ),
        mapping=_mapping(confidence=0.85),
    )

    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert audit.cells[0].hard_vetoes == []
    assert audit.cells[0].verify_score > 0.0
    assert "TEXT_PATH_MAPPING_CONFIDENCE_LOW" in audit.cells[0].reasoning


def test_pdf_with_vision_fallback_is_never_green() -> None:
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            vision_fallback_reason="Vision extraction timeout",
        )
    )
    assert audit.cells[0].classification != TrafficLight.GREEN
    assert "VISION_FALLBACK_TO_LEGACY_PARSER" in audit.cells[0].reasoning


def test_rule_score_below_verify_threshold_blocks_green() -> None:
    cv_result = CrossValidationResult()
    cv_result.add("warning", 0, "Design Count", "Synthetic warning")

    config = ScoringConfig(
        verify_green_threshold=0.95,
        green_threshold=0.90,
        soft_green_floor=0.70,
    )

    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="12"),
        cv_result=cv_result,
        config=config,
    )

    assert audit.cells[0].rule_score < config.verify_green_threshold
    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert audit.cells[0].final_score < 1.0
    assert "CHECK4_RULE_SCORE_BELOW_VERIFY_THRESHOLD" in audit.cells[0].reasoning


def test_check3_mismatch_forces_red() -> None:
    audit = _score(_bom(fmt=FileFormat.PDF, value="12", extracted="13"))
    assert audit.cells[0].classification == TrafficLight.RED
    assert "CHECK3_VALUE_MISMATCH" in audit.cells[0].hard_vetoes


def test_detail_number_value_mismatch_is_red() -> None:
    """BUG-014: Demo-Override entfernt; ein echter Wert-Widerspruch ist RED.

    Previously a hardcoded override released CHECK3_VALUE_MISMATCH for Detail
    Number fields when the extracted value contained '-' or '.'.  That path
    silently converted genuine mismatches (transformed="101A", PDF="1-01") into
    YELLOW, violating the zero-false-positive contract.  With the override gone,
    these cells are correctly RED.
    """
    schema = TargetSchema(
        fields=[
            TargetField(
                name="Detail Number",
                name_de="Positionsnummer",
                column="A",
                type="string",
                required=True,
            )
        ]
    )
    mapping = MappingResult(
        source_file="sample.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Pos",
                target_field="Detail Number",
                target_column="A",
                confidence=0.95,
                reasoning="Pos clearly maps to Detail Number",
                candidate_confidence=0.95,
                candidate_reasoning="Pos clearly maps to Detail Number",
            )
        ],
    )
    transform_result = TransformationResult(
        source_file="sample.pdf",
        customer="ACME",
        rows=[
            TransformedRow(
                row_index=0,
                cells=[
                    CellTransformation(
                        target_field="Detail Number",
                        target_column="A",
                        source_column="Pos",
                        raw_value="101A",
                        transformed_value="101A",
                        confidence=0.95,
                        method="passthrough",
                    )
                ],
            )
        ],
        source_is_pdf=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
        source_extraction_confidence=0.95,
        has_text_layer=True,
        check2_reason="text_layer_direct",
        source_locations={
            0: {
                "Pos": SourceLocation(
                    page=1,
                    bbox=[10.0, 20.0, 30.0, 40.0],
                    text="1-01",
                    match_type="column_corridor",
                )
            }
        },
    )

    audit = score_bom(transform_result, mapping, schema=schema)

    # BUG-014: a real value mismatch (transformed "101A" vs PDF "1-01") must be RED.
    assert audit.cells[0].classification == TrafficLight.RED
    assert "CHECK3_VALUE_MISMATCH" in audit.cells[0].hard_vetoes


def _single_field_pdf_audit(
    *,
    field_name: str,
    field_type: str,
    transformed_value: str,
    extracted_value: str,
    confidence: float,
    extraction_confidence: float = 0.95,
    source_match_type: str = "column_corridor",
    document_text_layer: str = "",
):
    schema = TargetSchema(
        fields=[
            TargetField(
                name=field_name,
                name_de=field_name,
                column="A",
                type=field_type,
                required=True,
            )
        ]
    )
    mapping = MappingResult(
        source_file="sample.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Src",
                target_field=field_name,
                target_column="A",
                confidence=0.95,
                reasoning=f"Src maps to {field_name}",
                candidate_confidence=0.95,
                candidate_reasoning=f"Src maps to {field_name}",
            )
        ],
    )
    transform_result = TransformationResult(
        source_file="sample.pdf",
        customer="ACME",
        rows=[
            TransformedRow(
                row_index=0,
                cells=[
                    CellTransformation(
                        target_field=field_name,
                        target_column="A",
                        source_column="Src",
                        raw_value=transformed_value,
                        transformed_value=transformed_value,
                        confidence=confidence,
                        method="passthrough",
                    )
                ],
            )
        ],
        source_is_pdf=True,
        extraction_method=ExtractionMethod.PYMUPDF_TEXT,
        source_extraction_confidence=extraction_confidence,
        has_text_layer=True,
        document_text_layer=document_text_layer,
        check2_reason="text_layer_direct",
        source_locations={
            0: {
                "Src": SourceLocation(
                    page=1,
                    bbox=[10.0, 20.0, 30.0, 40.0],
                    text=extracted_value,
                    match_type=source_match_type,
                )
            }
        },
    )

    audit = score_bom(transform_result, mapping, schema=schema)
    return audit.cells[0]


def _anchored_row_field_audit(
    *,
    target_field: str,
    target_field_type: str,
    target_value: str,
    anchor_field: str,
    anchor_value: str,
    document_text_layer: str,
    target_extracted_value: str = "",
    extraction_confidence: float = 0.95,
):
    schema = TargetSchema(
        fields=[
            TargetField(
                name=anchor_field,
                name_de=anchor_field,
                column="A",
                type="string",
                required=True,
            ),
            TargetField(
                name=target_field,
                name_de=target_field,
                column="B",
                type=target_field_type,
                required=True,
            ),
        ]
    )
    mapping = MappingResult(
        source_file="sample.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="AnchorSrc",
                target_field=anchor_field,
                target_column="A",
                confidence=0.95,
                reasoning=f"AnchorSrc maps to {anchor_field}",
                candidate_confidence=0.95,
                candidate_reasoning=f"AnchorSrc maps to {anchor_field}",
            ),
            ColumnMapping(
                source_column="TargetSrc",
                target_field=target_field,
                target_column="B",
                confidence=0.95,
                reasoning=f"TargetSrc maps to {target_field}",
                candidate_confidence=0.95,
                candidate_reasoning=f"TargetSrc maps to {target_field}",
            ),
        ],
    )
    transform_result = TransformationResult(
        source_file="sample.pdf",
        customer="ACME",
        rows=[
            TransformedRow(
                row_index=0,
                cells=[
                    CellTransformation(
                        target_field=anchor_field,
                        target_column="A",
                        source_column="AnchorSrc",
                        raw_value=anchor_value,
                        transformed_value=anchor_value,
                        confidence=0.95,
                        method="passthrough",
                    ),
                    CellTransformation(
                        target_field=target_field,
                        target_column="B",
                        source_column="TargetSrc",
                        raw_value=target_value,
                        transformed_value=target_value,
                        confidence=0.95,
                        method="passthrough",
                    ),
                ],
            )
        ],
        source_is_pdf=True,
        extraction_method=ExtractionMethod.GPT4O_VISION,
        source_extraction_confidence=extraction_confidence,
        has_text_layer=True,
        document_text_layer=document_text_layer,
        source_locations={
            0: {
                "AnchorSrc": SourceLocation(
                    page=1,
                    bbox=[10.0, 20.0, 30.0, 40.0],
                    text="",
                    match_type="column_estimate",
                ),
                "TargetSrc": SourceLocation(
                    page=1,
                    bbox=[40.0, 20.0, 60.0, 40.0],
                    text=target_extracted_value,
                    match_type="column_estimate",
                ),
            }
        },
    )

    audit = score_bom(transform_result, mapping, schema=schema)
    for cell in audit.cells:
        if cell.target_field == target_field:
            return cell
    raise AssertionError(f"Missing audit cell for {target_field}")


def test_detail_number_main_position_match_can_be_green() -> None:
    cell = _single_field_pdf_audit(
        field_name="Detail Number",
        field_type="string",
        transformed_value="1",
        extracted_value="1-01",
        confidence=0.95,
        extraction_confidence=0.95,
    )

    assert cell.classification == TrafficLight.GREEN
    assert cell.value_match_result == "match"
    assert "CHECK3_VALUE_MISMATCH" not in cell.reasoning


def test_detail_number_main_position_match_requires_high_extraction_confidence() -> (
    None
):
    comparator = ValueComparator()
    result = comparator.compare_values(
        mapped_value="1",
        extracted_value="1-01",
        target_field="Detail Number",
        extraction_confidence=0.89,
    )

    assert result.result == MatchResult.MISMATCH
    assert result.strict_exact_match is False


def test_dimension_semantic_numeric_match_can_be_green() -> None:
    cell = _single_field_pdf_audit(
        field_name="Dimensions X/D",
        field_type="decimal",
        transformed_value="20",
        extracted_value="2zu0 mm",
        confidence=0.95,
        extraction_confidence=0.95,
    )

    assert cell.classification == TrafficLight.GREEN
    assert cell.value_match_result == "match"
    assert cell.value_match_detail == "dimension semantic numeric match"


def test_dimension_semantic_numeric_match_requires_high_extraction_confidence() -> None:
    """Garbled extracted value at low confidence must not match.

    "2zu0 mm" tokenises as a pseudo-combined string whose positional component
    contradicts the mapped value — a deterministic MISMATCH at any confidence
    (numeric contradictions are vetoes; only the MATCH direction is gated on
    high extraction confidence).
    """
    comparator = ValueComparator()
    result = comparator.compare_values(
        mapped_value="20",
        extracted_value="2zu0 mm",
        target_field="Dimensions X/D",
        extraction_confidence=0.89,
    )

    assert result.result == MatchResult.MISMATCH
    assert result.strict_exact_match is False


def test_description_semantic_core_match_can_be_green() -> None:
    cell = _single_field_pdf_audit(
        field_name="Description",
        field_type="string",
        transformed_value="Zylinderstift DIN 7979",
        extracted_value="Zylinderstift DIN7979 gehärtet",
        confidence=0.95,
        extraction_confidence=0.95,
    )

    assert cell.classification == TrafficLight.GREEN
    assert cell.value_match_result == "match"
    assert cell.value_match_detail == "description semantic core match"


def test_description_semantic_core_match_requires_high_extraction_confidence() -> None:
    comparator = ValueComparator()
    result = comparator.compare_values(
        mapped_value="Zylinderstift DIN 7979",
        extracted_value="Zylinderstift DIN7979 gehärtet",
        target_field="Description",
        extraction_confidence=0.89,
    )

    assert result.result == MatchResult.MISMATCH
    assert result.strict_exact_match is False


def test_customer_part_number_global_text_verification_stays_yellow() -> None:
    """BUG-004: document-global presence of a part number is NOT row-locked
    identity proof — the value of another row would verify this cell. The
    match keeps the cell out of RED, but GREEN is withheld (category A needs
    strict exact evidence)."""
    cell = _single_field_pdf_audit(
        field_name="Customer Part Number",
        field_type="string",
        transformed_value="AB-123_45",
        extracted_value="",
        confidence=0.95,
        extraction_confidence=0.95,
        source_match_type="column_estimate",
        document_text_layer="Header\nCustomer Part Number: AB 123 45\nFooter",
    )

    assert cell.classification == TrafficLight.YELLOW
    assert cell.value_match_result == "match"
    assert (
        cell.value_match_detail
        == "customer part number verified via global pdf text layer"
    )
    assert cell.check2_reason == "global_pdf_text_layer"
    assert cell.pdf_extraction_confidence == 0.95


def test_customer_part_number_wrong_value_stays_red() -> None:
    cell = _single_field_pdf_audit(
        field_name="Customer Part Number",
        field_type="string",
        transformed_value="AB-123_45",
        extracted_value="ZX-999_00",
        confidence=0.95,
        extraction_confidence=0.95,
        document_text_layer="Header\nCustomer Part Number: ZX 999 00\nFooter",
    )

    assert cell.classification == TrafficLight.RED
    assert "CHECK3_VALUE_MISMATCH" in cell.hard_vetoes


def test_quantity_semantic_integer_match_can_be_green() -> None:
    cell = _single_field_pdf_audit(
        field_name="Design Count",
        field_type="integer",
        transformed_value="1",
        extracted_value="1,00 Stk",
        confidence=0.95,
        extraction_confidence=0.95,
    )

    assert cell.classification == TrafficLight.GREEN
    assert cell.value_match_result == "match"
    assert cell.value_match_detail == "quantity semantic integer match"


def test_quantity_semantic_integer_mismatch_stays_red() -> None:
    cell = _single_field_pdf_audit(
        field_name="Design Count",
        field_type="integer",
        transformed_value="1",
        extracted_value="2 Stk",
        confidence=0.95,
        extraction_confidence=0.95,
    )

    assert cell.classification == TrafficLight.RED
    assert "CHECK3_VALUE_MISMATCH" in cell.hard_vetoes


def test_design_count_global_text_anchor_match_stays_yellow() -> None:
    """BUG-001: the global-text anchor fallback is weak, row-unlocked evidence
    that echoes the value under test back as its own "extraction". Without an
    independent master-data confirmation it must not carry GREEN — the match
    evidence is kept (no RED), the reviewer confirms."""
    cell = _anchored_row_field_audit(
        target_field="Design Count",
        target_field_type="integer",
        target_value="1",
        anchor_field="Detail Number",
        anchor_value="101",
        document_text_layer="Page 1\n101 ANGLE PLATE 1 STK\nPage 2",
    )

    # BUG-002: the 3-line window also contains "Page 2" — two quantity-shaped
    # candidates make the context ambiguous, so the fallback extraction itself
    # declines (no confirmation bias towards the expected value).
    assert cell.classification == TrafficLight.YELLOW
    assert cell.check2_reason == "no_coordinate_match"
    assert cell.pdf_extracted_value is None


def test_design_count_global_anchor_handles_leading_tokens_before_position() -> None:
    """BUG-001: the anchor fallback still extracts correctly (leading tokens are
    skipped), but without an independent master-data confirmation it carries
    review-evidence only — YELLOW, never GREEN on its own."""
    cell = _anchored_row_field_audit(
        target_field="Design Count",
        target_field_type="integer",
        target_value="1",
        anchor_field="Detail Number",
        anchor_value="2-040",
        document_text_layer="67 2-040 ANGLE PLATE 1 STK",
    )

    assert cell.classification == TrafficLight.YELLOW
    assert "ANCHOR_FALLBACK_REQUIRES_MASTER_DATA" in cell.reasoning
    assert cell.check2_reason == "global_text_row_anchor"
    assert cell.pdf_extracted_value == "1"


def test_design_count_global_text_anchor_uses_customer_part_number() -> None:
    cell = _anchored_row_field_audit(
        target_field="Design Count",
        target_field_type="integer",
        target_value="1",
        anchor_field="Customer Part Number",
        anchor_value="AB-123_45",
        document_text_layer="Header\nAB 123 45 ANGLE PLATE 1 STK\nFooter",
    )

    # BUG-001: anchor-fallback evidence alone never carries GREEN.
    assert cell.classification == TrafficLight.YELLOW
    assert "ANCHOR_FALLBACK_REQUIRES_MASTER_DATA" in cell.reasoning
    assert cell.check2_reason == "global_text_row_anchor"
    assert "anchor_field=Customer Part Number" in cell.pdf_source_location


def test_design_count_global_text_anchor_mismatch_stays_red() -> None:
    cell = _anchored_row_field_audit(
        target_field="Design Count",
        target_field_type="integer",
        target_value="1",
        anchor_field="Detail Number",
        anchor_value="101",
        document_text_layer="Page 1\n101 ANGLE PLATE 2 STK\nPage 2",
    )

    assert cell.classification == TrafficLight.RED
    assert "CHECK3_VALUE_MISMATCH" in cell.hard_vetoes


def test_value_mismatch_on_description_and_dimension_fields_is_red() -> None:
    """BUG-014: Demo-Override entfernt; ein echter Wert-Widerspruch ist RED.

    Previously a hardcoded frontend_ui_pass_through override promoted RED cells
    with CHECK3_VALUE_MISMATCH to YELLOW for Description/Dimensions fields when
    extraction confidence >= 0.90.  That silently discarded real contradictions.
    With the override gone, value mismatches on these fields are correctly RED.
    """
    test_cases = [
        ("Description", "string", "ANGLE PLATE A", "ANGLE PLATE B"),
        ("Dimensions X/D", "decimal", "10", "11 mm"),
        ("Dimensions Y/L", "decimal", "20", "21 mm"),
        ("Dimensions Z", "decimal", "30", "31 mm"),
    ]

    for field_name, field_type, transformed_value, extracted_value in test_cases:
        cell = _single_field_pdf_audit(
            field_name=field_name,
            field_type=field_type,
            transformed_value=transformed_value,
            extracted_value=extracted_value,
            confidence=0.95,
        )

        # BUG-014: a real value mismatch must be RED regardless of field type or confidence.
        assert cell.classification == TrafficLight.RED, (
            f"{field_name}: expected RED but got {cell.classification}"
        )
        assert "CHECK3_VALUE_MISMATCH" in cell.hard_vetoes
        assert "frontend_ui_pass_through_high_confidence" not in cell.rule_details


def test_value_mismatch_below_confidence_threshold_also_red() -> None:
    """BUG-014: Demo-Override entfernt; Wert-Widerspruch ist RED bei jeder Konfidenz."""
    cell = _single_field_pdf_audit(
        field_name="Description",
        field_type="string",
        transformed_value="ANGLE PLATE A",
        extracted_value="ANGLE PLATE B",
        confidence=0.89,
    )

    assert cell.classification == TrafficLight.RED
    assert "CHECK3_VALUE_MISMATCH" in cell.hard_vetoes


# --- GREEN-RECOVERY P0/P1: row_fallback is whole-row text, not column evidence ---

_AV_ROW = (
    "451 03 2 DISTANZLEISTE ES QGDAGE0115746 STAHL EN 10305-2- 1.0580 459 x 150 x 200"
)


def test_row_fallback_strong_identity_present_in_row_is_green() -> None:
    """A distinctive value (>=6 core chars) present as a bounded token in its own
    row line is row-scoped identity proof — GREEN, even though the parser could
    not isolate the column (match_type=row_fallback => extracted = whole row)."""
    cell = _single_field_pdf_audit(
        field_name="Description",
        field_type="string",
        transformed_value="DISTANZLEISTE ES",
        extracted_value=_AV_ROW,
        confidence=0.95,
        source_match_type="row_fallback",
    )
    assert cell.classification == TrafficLight.GREEN
    assert cell.value_match_result == "match"
    assert "CHECK3_VALUE_MISMATCH" not in cell.hard_vetoes


def test_row_fallback_category_a_strong_identity_is_green() -> None:
    """Category-A field (Customer Part Number): a long unique token confirmed
    within its own row earns strict-exact identity and therefore GREEN."""
    cell = _single_field_pdf_audit(
        field_name="Customer Part Number",
        field_type="string",
        transformed_value="QGDAGE0115746",
        extracted_value=_AV_ROW,
        confidence=0.95,
        source_match_type="row_fallback",
    )
    assert cell.classification == TrafficLight.GREEN


def test_row_fallback_short_numeric_present_stays_yellow_not_green_not_red() -> None:
    """A bare 3-digit dimension recurs across a BOM row, so its presence is NOT
    identity proof: it must stay YELLOW (review) — never GREEN, never RED."""
    cell = _single_field_pdf_audit(
        field_name="Dimensions X/D",
        field_type="decimal",
        transformed_value="459",
        extracted_value=_AV_ROW,
        confidence=0.95,
        source_match_type="row_fallback",
    )
    assert cell.classification == TrafficLight.YELLOW
    assert "CHECK3_VALUE_MISMATCH" not in cell.hard_vetoes


def test_row_fallback_value_absent_from_row_is_yellow_not_red() -> None:
    """When the value is NOT in the row text, the whole-row blob still cannot
    CONTRADICT a single column value — so it is UNCERTAIN (YELLOW), never a RED
    hard-veto. (A real contradiction needs an isolated-column extraction.)"""
    cell = _single_field_pdf_audit(
        field_name="Material",
        field_type="string",
        transformed_value="AlSi9Cu3",
        extracted_value=_AV_ROW,  # contains STAHL 1.0580, not AlSi9Cu3
        confidence=0.95,
        source_match_type="row_fallback",
    )
    assert cell.classification == TrafficLight.YELLOW
    assert "CHECK3_VALUE_MISMATCH" not in cell.hard_vetoes


def test_row_fallback_exact_equal_value_is_green() -> None:
    """Degenerate row_fallback whose row text equals the value exactly is a
    genuine exact match (preserves the digital-confidence contract)."""
    cell = _single_field_pdf_audit(
        field_name="Design Count",
        field_type="integer",
        transformed_value="12",
        extracted_value="12",
        confidence=0.95,
        source_match_type="row_fallback",
    )
    assert cell.classification == TrafficLight.GREEN


def test_blocking_validator_error_caps_to_yellow_not_red_when_values_match() -> None:
    """A MAPPING_VALIDATOR_ERROR flags the whole COLUMN, not the cell value.

    Policy (intentional): a valued, value-matched cell in a validator-flagged
    column is capped at YELLOW (review) — NOT forced to RED. Forcing RED here
    destroyed clean, value-matched cells wholesale (e.g. Design Count 114/114 RED
    on a real export). The zero-false-GREEN guarantee is what matters and is fully
    preserved: the cell must never be GREEN. (Contrast: a per-cell HARD VETO still
    forces RED — see the test directly below.)
    """
    validation = ValidationResult(
        issues=[
            ValidationIssue(
                severity="error",
                message="Synthetic blocking error",
                target_field="Design Count",
            )
        ]
    )

    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="12"),
        mapping_validation=validation,
    )
    # The guarantee: never GREEN.
    assert audit.cells[0].classification != TrafficLight.GREEN
    # The new policy: a valued cell is YELLOW (review), not RED.
    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert "MAPPING_VALIDATOR_ERROR" in audit.cells[0].blocking_errors


def test_hard_veto_from_pdf_flag_forces_red_even_when_values_match() -> None:
    audit = _score(
        _bom(
            fmt=FileFormat.PDF,
            value="12",
            extracted="12",
            row_validation_flags={0: ["DUAL:Qty: mismatch"]},
        )
    )
    assert audit.cells[0].classification == TrafficLight.RED
    assert "PDF_DUAL_MISMATCH" in audit.cells[0].hard_vetoes


def test_true_pdf_exact_match_can_be_green() -> None:
    stub = _StubCounterCheckService(passed=True)
    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="12"),
        config=_counter_check_config(),
        counter_check_service=stub,
        job_id="job-true-green",
        pdf_path=Path("sample.pdf"),
    )
    assert audit.cells[0].classification == TrafficLight.GREEN
    assert "CHECK3_MATCH" in audit.cells[0].green_evidence
    assert stub.calls == 1


def test_counter_check_failure_blocks_green_candidate() -> None:
    stub = _StubCounterCheckService(passed=False)
    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="12"),
        config=_counter_check_config(),
        counter_check_service=stub,
        job_id="job-cc-fail",
        pdf_path=Path("sample.pdf"),
    )

    assert stub.calls == 1
    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert audit.cells[0].counter_check_score == 0.0
    # PERF-002: the failed batch counter-check leaves the cell un-promoted;
    # the verdict lives in the counter_check fields, not the reasoning string.
    assert "stub_secondary" in audit.cells[0].counter_check_notes
    assert "PROMOTED_BY_BATCH_COUNTER_CHECK" not in audit.cells[0].reasoning


def test_counter_check_success_allows_green_candidate() -> None:
    stub = _StubCounterCheckService(passed=True)
    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="12"),
        config=_counter_check_config(),
        counter_check_service=stub,
        job_id="job-cc-pass",
        pdf_path=Path("sample.pdf"),
    )

    assert stub.calls == 1
    assert audit.cells[0].classification == TrafficLight.GREEN
    assert audit.cells[0].counter_check_score == 1.0


def test_counter_check_disabled_in_config_skips_service_call() -> None:
    stub = _StubCounterCheckService(passed=False)
    config = ScoringConfig(enable_counter_check=False)

    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="12"),
        config=config,
        counter_check_service=stub,
        job_id="job-cc-disabled",
        pdf_path=Path("sample.pdf"),
    )

    assert stub.calls == 0
    assert audit.cells[0].classification == TrafficLight.GREEN
    assert audit.cells[0].counter_check_notes == "counter_check_not_enabled"
    assert not any(
        evidence.startswith("CHECK5_") for evidence in audit.cells[0].green_evidence
    )


def test_counter_check_not_called_when_pre_gate_fails() -> None:
    stub = _StubCounterCheckService(passed=True)
    audit = _score(
        _bom(fmt=FileFormat.PDF, value="12", extracted="13"),
        counter_check_service=stub,
        job_id="job-cc-skip",
        pdf_path=Path("sample.pdf"),
    )

    assert stub.calls == 0
    assert audit.cells[0].classification == TrafficLight.RED


def test_manual_edit_results_in_manual_confirmed_not_green() -> None:
    audit = _score(_bom(fmt=FileFormat.PDF, value="12", extracted="13"))
    assert audit.cells[0].classification == TrafficLight.RED

    apply_cell_edits(
        audit,
        [
            CellEditRequest(
                row_index=0,
                target_field="Design Count",
                corrected_value="12",
            )
        ],
    )

    assert audit.cells[0].classification == TrafficLight.MANUAL_CONFIRMED
    assert audit.green_count == 0
    assert audit.manual_confirmed_count == 1
