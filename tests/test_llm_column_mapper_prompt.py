from __future__ import annotations

from src.core.models import FileFormat, ParsedBOM, SourceMetadata
from src.mapping.llm_column_mapper import build_mapping_prompt
from src.mapping.schema_registry import load_schema


def test_build_mapping_prompt_allows_literal_json_braces() -> None:
    bom = ParsedBOM(
        source=SourceMetadata(
            filename="demo.pdf",
            filepath="demo.pdf",
            customer="Demo Customer",
            format=FileFormat.PDF,
        ),
        headers=["Part No.", "Description"],
        rows=[
            {
                "Part No.": "A-123",
                "Description": "Formplatte",
            }
        ],
    )

    system_prompt, user_prompt = build_mapping_prompt(bom, load_schema())

    assert '"mappings": [' in system_prompt
    assert "Part No." in system_prompt
    assert "Description" in system_prompt
    assert "Map the 2 source columns" in user_prompt
