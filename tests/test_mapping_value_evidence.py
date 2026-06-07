"""Fix C/D: value-evidence confidence boost + dimension-component propagation, and
the two false-alarm validator checks that capped correct material/dimension greens."""

from __future__ import annotations

from src.core.models import FileFormat, ParsedBOM, SourceMetadata
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.mapping_validator import validate_mapping
from src.mapping.schema_registry import TargetField, TargetSchema


def _bom(headers: list[str], rows: list[dict[str, str]]) -> ParsedBOM:
    return ParsedBOM(
        source=SourceMetadata(filename="t.pdf", filepath="t.pdf", format=FileFormat.PDF),
        headers=headers,
        rows=rows,
    )


def _schema() -> TargetSchema:
    spec = [
        ("Material", "K", "string"),
        ("Dimensions X/D", "M", "decimal"),
        ("Dimensions Y/L", "N", "decimal"),
        ("Dimensions Z", "O", "decimal"),
    ]
    return TargetSchema(
        fields=[
            TargetField(name=n, name_de=n, column=c, type=t, required=False)
            for n, c, t in spec
        ]
    )


def _map(source: str, target: str, conf: float) -> ColumnMapping:
    return ColumnMapping(
        source_column=source,
        target_field=target,
        target_column="K",
        confidence=conf,
        reasoning="llm",
        candidate_confidence=conf,
        candidate_reasoning="llm",
    )


def test_material_column_confidence_is_boosted_by_value_evidence() -> None:
    bom = _bom(["Werkst"], [{"Werkst": v} for v in ["1.2343", "1.2738", "1.0037", "VGN"]])
    mp = MappingResult(source_file="t", customer="c", mappings=[_map("Werkst", "Material", 0.80)])
    result = validate_mapping(mp, bom, _schema())
    mat = next(m for m in result.adjusted_mappings if m.target_field == "Material")
    assert mat.candidate_confidence >= 0.92


def test_non_material_column_is_not_boosted() -> None:
    """A column of prose mapped to Material must NOT be boosted (no value evidence)."""
    bom = _bom(["Bez"], [{"Bez": v} for v in ["Formplatte", "Schieber", "Kern", "Einsatz"]])
    mp = MappingResult(source_file="t", customer="c", mappings=[_map("Bez", "Material", 0.80)])
    result = validate_mapping(mp, bom, _schema())
    mat = next(m for m in result.adjusted_mappings if m.target_field == "Material")
    assert mat.candidate_confidence == 0.80  # unchanged


def test_combined_dimension_mapping_propagates_to_components() -> None:
    bom = _bom(["Maße"], [{"Maße": v} for v in ["10x20x30", "40x50x60", "70x80x90"]])
    mp = MappingResult(
        source_file="t", customer="c", mappings=[_map("Maße", "Dimensions X/D", 0.80)]
    )
    result = validate_mapping(mp, bom, _schema())
    targets = {m.target_field for m in result.adjusted_mappings}
    assert {"Dimensions X/D", "Dimensions Y/L", "Dimensions Z"} <= targets
    for m in result.adjusted_mappings:
        if m.target_field in {"Dimensions Y/L", "Dimensions Z"}:
            assert m.candidate_confidence >= 0.92


def test_combined_dimension_is_not_a_type_error() -> None:
    """Check 3 must not flag a combined-dim source on a decimal field as an error."""
    bom = _bom(["Maße"], [{"Maße": v} for v in ["10x20x30", "40x50x60"]])
    mp = MappingResult(
        source_file="t", customer="c", mappings=[_map("Maße", "Dimensions X/D", 0.80)]
    )
    result = validate_mapping(mp, bom, _schema())
    errors = [i for i in result.issues if i.severity == "error" and i.target_field == "Dimensions X/D"]
    assert errors == []


def test_material_multi_code_is_info_not_capping_warning() -> None:
    """Many distinct Werkstoff codes is normal — must be info, never a warning."""
    bom = _bom(["Werkst"], [{"Werkst": v} for v in ["1.2343", "1.2738", "1.0037"]])
    mp = MappingResult(source_file="t", customer="c", mappings=[_map("Werkst", "Material", 0.80)])
    result = validate_mapping(mp, bom, _schema())
    warnings = [i for i in result.issues if i.severity == "warning" and i.target_field == "Material"]
    assert warnings == []
