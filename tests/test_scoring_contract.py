from __future__ import annotations

from src.core.models import FileFormat, ParsedBOM, SourceMetadata
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.scoring.ensemble_scorer import score_bom
from src.scoring.threshold_manager import TrafficLight
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
