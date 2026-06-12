from __future__ import annotations

from src.core.models import FileFormat, ParsedBOM, SourceMetadata
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.scoring.ensemble_scorer import score_bom
from src.scoring.threshold_manager import ScoringConfig, TrafficLight, validate_contract
from src.transform.pipeline import transform_bom


def test_scanned_pdf_without_text_layer_never_gets_green_from_missing_verify_evidence() -> (
    None
):
    schema = TargetSchema(
        fields=[
            TargetField(
                name="Design Count",
                name_de="Anzahl",
                column="A",
                type="integer",
                required=True,
            )
        ]
    )
    bom = ParsedBOM(
        source=SourceMetadata(
            filename="scan.pdf",
            filepath="scan.pdf",
            customer="ACME",
            format=FileFormat.PDF,
        ),
        headers=["Qty"],
        rows=[{"Qty": "12"}],
        metadata={"has_text_layer": False},
    )
    mapping = MappingResult(
        source_file="scan.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Qty",
                target_field="Design Count",
                target_column="A",
                confidence=0.99,
            )
        ],
    )

    audit = score_bom(
        transform_bom(bom, mapping, schema=schema), mapping, schema=schema
    )

    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert audit.cells[0].value_match_result == "uncertain"


def test_non_pdf_never_gets_green_under_zero_false_positive_contract() -> None:
    schema = TargetSchema(
        fields=[
            TargetField(
                name="Design Count",
                name_de="Anzahl",
                column="A",
                type="integer",
                required=True,
            )
        ]
    )
    bom = ParsedBOM(
        source=SourceMetadata(
            filename="structured.xlsx",
            filepath="structured.xlsx",
            customer="ACME",
            format=FileFormat.EXCEL,
        ),
        headers=["Qty"],
        rows=[{"Qty": "12"}],
    )
    mapping = MappingResult(
        source_file="structured.xlsx",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Qty",
                target_field="Design Count",
                target_column="A",
                confidence=0.99,
            )
        ],
    )

    audit = score_bom(
        transform_bom(bom, mapping, schema=schema), mapping, schema=schema
    )

    assert audit.cells[0].classification == TrafficLight.YELLOW
    assert "NO_PDF_EVIDENCE" in audit.cells[0].reasoning


# ---------------------------------------------------------------------------
# BUG-005: validate_contract (FIX 7)
# ---------------------------------------------------------------------------


def test_validate_contract_default_config_has_no_deviations() -> None:
    """Default ScoringConfig() meets every contract requirement."""
    assert validate_contract(ScoringConfig()) == []


def test_validate_contract_reports_disabled_counter_check_and_low_threshold() -> None:
    """Two deviations are reported for a config that disables counter-check
    and lowers verify_green_threshold below the contract minimum."""
    cfg = ScoringConfig(enable_counter_check=False, verify_green_threshold=0.90)
    deviations = validate_contract(cfg)
    assert len(deviations) == 2
    assert any("Counter-Check" in d for d in deviations)
    assert any("verify_green_threshold" in d and "0.9" in d for d in deviations)


def test_validate_contract_reports_conservative_mode_off() -> None:
    cfg = ScoringConfig(conservative_mode=False)
    deviations = validate_contract(cfg)
    assert any("conservative_mode" in d for d in deviations)


def test_validate_contract_reports_low_extraction_confidence() -> None:
    cfg = ScoringConfig(green_extraction_min_confidence=0.70)
    deviations = validate_contract(cfg)
    assert any("green_extraction_min_confidence" in d for d in deviations)


def test_validate_contract_reports_soft_vetoes_as_yellow() -> None:
    cfg = ScoringConfig(soft_vetoes_as_yellow=True)
    deviations = validate_contract(cfg)
    assert any("soft_vetoes_as_yellow" in d for d in deviations)
