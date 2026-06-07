"""Schema Registry — loads and provides the target schema definition.

Reads the target schema from config/target_schema.json and exposes
it as structured Pydantic models for use by the mapping layer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SCHEMA_PATH = _PROJECT_ROOT / "config" / "target_schema.json"


class TargetField(BaseModel):
    """A single field in the target schema."""

    name: str
    name_de: str = ""
    column: str
    type: str = "string"
    required: bool = False
    description: str = ""
    description_de: str = ""
    examples: list[str] = Field(default_factory=list)
    master_data_lookup: str | None = None


class TemplateInfo(BaseModel):
    """Workbook-specific layout metadata for the target template."""

    name: str = ""
    sheet: str = "Stückliste"
    header_row: int = 5
    data_start_row: int = 7
    meta_rows: dict[str, str] = Field(default_factory=dict)


class TargetSchema(BaseModel):
    """The full target schema definition."""

    schema_version: str = "1.0"
    template_info: TemplateInfo = Field(default_factory=TemplateInfo)
    fields: list[TargetField] = Field(default_factory=list)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def required_fields(self) -> list[TargetField]:
        return [f for f in self.fields if f.required]

    @property
    def field_by_column(self) -> dict[str, TargetField]:
        return {f.column: f for f in self.fields}

    @property
    def field_by_name(self) -> dict[str, TargetField]:
        return {f.name: f for f in self.fields}

    def to_prompt_description(self) -> str:
        """Build a compact description of all target fields for LLM prompts."""
        lines: list[str] = []
        for f in self.fields:
            req = " [REQUIRED]" if f.required else ""
            ex = ""
            if f.examples:
                ex = f"  Examples: {', '.join(f.examples[:3])}"
            lines.append(
                f'- Column {f.column}: "{f.name}" / "{f.name_de}"{req}\n'
                f"  {f.description}\n"
                f"  Type: {f.type}{ex}"
            )
        return "\n".join(lines)


def load_schema(path: Path | str | None = None) -> TargetSchema:
    """Load the target schema from a JSON file.

    Args:
        path: Path to the schema JSON file. Defaults to config/target_schema.json.

    Returns:
        TargetSchema with all fields populated.
    """
    schema_path = Path(path) if path else _DEFAULT_SCHEMA_PATH

    if not schema_path.exists():
        raise FileNotFoundError(f"Target schema not found at {schema_path}")

    logger.info("Loading target schema from %s", schema_path)
    data = json.loads(schema_path.read_text(encoding="utf-8"))

    fields = [TargetField(**f) for f in data.get("fields", [])]
    schema = TargetSchema(
        schema_version=data.get("schema_version", "1.0"),
        template_info=TemplateInfo(**data.get("template_info", {})),
        fields=fields,
    )

    logger.info(
        "Loaded %d target fields (%d required)",
        len(fields),
        len(schema.required_fields),
    )
    return schema
