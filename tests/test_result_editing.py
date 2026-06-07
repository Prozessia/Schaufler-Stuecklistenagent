from __future__ import annotations

from pathlib import Path

from src.api.cell_edits import apply_cell_edits
from src.api.job_store import Job
from src.api.models.schemas import CellEditRequest
from src.api.result_builder import build_job_result
from src.core.models import FileFormat, ParsedBOM, SourceLocation, SourceMetadata
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.scoring.ensemble_scorer import score_bom
from src.scoring.audit_trail import BomAuditTrail, CellAudit
from src.scoring.threshold_manager import TrafficLight
from src.transform.pipeline import transform_bom


def test_build_job_result_sorts_excel_columns_naturally() -> None:
    audit = BomAuditTrail(
        source_file="example.xlsx",
        customer="ACME",
        cells=[
            CellAudit(
                row_index=1,
                target_field="AA Field",
                target_column="AA",
                transformed_value="late",
                classification=TrafficLight.YELLOW,
                final_score=0.7,
            ),
            CellAudit(
                row_index=1,
                target_field="B Field",
                target_column="B",
                transformed_value="middle",
                classification=TrafficLight.GREEN,
                final_score=0.9,
            ),
            CellAudit(
                row_index=1,
                target_field="A Field",
                target_column="A",
                transformed_value="first",
                classification=TrafficLight.GREEN,
                final_score=0.95,
            ),
        ],
        green_count=2,
        yellow_count=1,
        red_count=0,
        neutral_count=0,
        total_scored=3,
    )
    job = Job(
        job_id="job-1",
        filename="example.xlsx",
        filepath=Path("example.xlsx"),
        customer="ACME",
        status="completed",
        audit=audit,
    )

    schema = TargetSchema(
        fields=[
            TargetField(name="AA Field", name_de="AA Feld", column="AA"),
            TargetField(name="B Field", name_de="B Feld", column="B"),
            TargetField(name="A Field", name_de="A Feld", column="A"),
        ]
    )

    result = build_job_result(
        job.job_id, job, schema=schema, template_path=Path("missing-template.xlsx")
    )

    assert result.target_fields == ["A Field", "B Field", "AA Field"]
    assert [column.column for column in result.columns] == ["A", "B", "AA"]
    assert result.columns[0].header_lines == ["A Field", "A Feld"]
    assert [cell.target_field for cell in result.rows[0].cells] == [
        "A Field",
        "B Field",
        "AA Field",
    ]


def test_apply_cell_edits_updates_existing_and_missing_cells() -> None:
    audit = BomAuditTrail(
        source_file="example.xlsx",
        customer="ACME",
        cells=[
            CellAudit(
                row_index=1,
                target_field="Position",
                target_column="A",
                transformed_value="1",
                classification=TrafficLight.GREEN,
                final_score=0.99,
            ),
            CellAudit(
                row_index=1,
                target_field="Benennung",
                target_column="C",
                transformed_value=None,
                classification=TrafficLight.RED,
                final_score=0.2,
            ),
            CellAudit(
                row_index=2,
                target_field="Werkstoff",
                target_column="D",
                transformed_value=None,
                classification=TrafficLight.NEUTRAL,
                final_score=0.0,
            ),
        ],
        green_count=1,
        yellow_count=0,
        red_count=1,
        neutral_count=1,
        total_scored=2,
    )

    apply_cell_edits(
        audit,
        [
            CellEditRequest(
                row_index=1, target_field="Benennung", corrected_value="Formplatte"
            ),
            CellEditRequest(
                row_index=1, target_field="Werkstoff", corrected_value="1.2343"
            ),
        ],
    )

    lookup = {(cell.row_index, cell.target_field): cell for cell in audit.cells}

    assert lookup[(1, "Benennung")].transformed_value == "Formplatte"
    assert lookup[(1, "Benennung")].classification == TrafficLight.MANUAL_CONFIRMED
    assert lookup[(1, "Benennung")].transform_method == "manual_override"

    assert lookup[(1, "Werkstoff")].transformed_value == "1.2343"
    assert lookup[(1, "Werkstoff")].classification == TrafficLight.MANUAL_CONFIRMED
    assert lookup[(1, "Werkstoff")].target_column == "D"

    assert audit.green_count == 1
    assert audit.yellow_count == 0
    assert audit.red_count == 0
    assert audit.neutral_count == 1
    assert audit.manual_confirmed_count == 2
    assert audit.total_scored == 3


def test_source_locations_propagate_from_transform_to_api_result() -> None:
    schema = TargetSchema(
        fields=[TargetField(name="Artikelnummer", name_de="Artikelnummer", column="B")]
    )
    bom = ParsedBOM(
        source=SourceMetadata(
            filename="example.pdf",
            filepath="example.pdf",
            customer="ACME",
            format=FileFormat.PDF,
        ),
        headers=["Part No"],
        rows=[{"Part No": "A-123"}],
        metadata={
            "has_text_layer": True,
            "source_locations": {
                0: {
                    "Part No": {
                        "page": 2,
                        "bbox": [10.0, 20.0, 30.0, 40.0],
                        "text": "A-123",
                        "match_type": "column_corridor",
                    }
                }
            },
        },
    )
    mapping = MappingResult(
        source_file="example.pdf",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Part No",
                target_field="Artikelnummer",
                target_column="B",
                confidence=0.95,
            )
        ],
    )

    transform_result = transform_bom(bom, mapping, schema=schema)
    assert transform_result.source_locations[0]["Part No"] == SourceLocation(
        page=2,
        bbox=[10.0, 20.0, 30.0, 40.0],
        text="A-123",
        match_type="column_corridor",
    )

    audit = score_bom(transform_result, mapping, schema=schema)
    assert audit.cells[0].source_location == SourceLocation(
        page=2,
        bbox=[10.0, 20.0, 30.0, 40.0],
        text="A-123",
        match_type="column_corridor",
    )

    job = Job(
        job_id="job-source-location",
        filename="example.pdf",
        filepath=Path("example.pdf"),
        customer="ACME",
        status="completed",
        audit=audit,
    )
    result = build_job_result(
        job.job_id,
        job,
        schema=schema,
        template_path=Path("missing-template.xlsx"),
    )

    assert result.rows[0].cells[0].source_location == SourceLocation(
        page=2,
        bbox=[10.0, 20.0, 30.0, 40.0],
        text="A-123",
        match_type="column_corridor",
    )
