"""LLM-based column mapper — maps source BOM columns to target schema.

Uses Azure OpenAI GPT-4o to semantically map arbitrary source columns
(any language, any format) to the Schaufler target schema.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.config_loader import load_app_config
from src.core.models import ParsedBOM
from src.core.statuses import FinalStatus, MatchResult
from src.llm.base import BaseLLM, LLMResponse
from src.mapping.schema_registry import TargetSchema, load_schema

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


# ---------------------------------------------------------------------------
# Data models for mapping results
# ---------------------------------------------------------------------------


class ColumnMapping(BaseModel):
    """A single source→target column mapping."""

    source_column: str
    target_field: str | None = None
    target_column: str | None = None
    confidence: float = 0.0
    reasoning: str = ""
    candidate_confidence: float = 0.0
    candidate_reasoning: str = ""
    pdf_extracted_value: str | None = None
    pdf_extraction_confidence: float = 0.0
    pdf_source_location: str = ""
    value_match_result: MatchResult = MatchResult.UNCERTAIN
    value_match_detail: str = ""
    blocking_errors: list[str] = Field(default_factory=list)
    hard_vetoes: list[str] = Field(default_factory=list)
    final_status: FinalStatus | None = None
    green_evidence: list[str] = Field(default_factory=list)
    field_category: str = ""


class MappingResult(BaseModel):
    """Complete mapping result for a single BOM."""

    source_file: str = ""
    customer: str = ""
    mappings: list[ColumnMapping] = Field(default_factory=list)
    unmapped_target_fields: list[str] = Field(default_factory=list)
    notes: str = ""
    llm_response: LLMResponse | None = None

    @property
    def mapped_count(self) -> int:
        return sum(1 for m in self.mappings if m.target_field is not None)

    @property
    def total_source_columns(self) -> int:
        return len(self.mappings)

    @property
    def avg_confidence(self) -> float:
        mapped = [m.confidence for m in self.mappings if m.target_field is not None]
        return sum(mapped) / len(mapped) if mapped else 0.0

    def get_mapping_for_target(self, target_field: str) -> ColumnMapping | None:
        """Find the mapping that maps to a specific target field."""
        for m in self.mappings:
            if m.target_field == target_field:
                return m
        return None

    def get_mapping_for_source(self, source_column: str) -> ColumnMapping | None:
        """Find the mapping for a specific source column."""
        for m in self.mappings:
            if m.source_column == source_column:
                return m
        return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _load_prompt_template() -> str:
    """Load the column mapping prompt template."""
    path = _PROMPTS_DIR / "column_mapping.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_domain_context() -> str:
    """Load domain context from app_config.yaml + overrides.yaml."""
    return str(load_app_config().get("domain_context", ""))


def _format_source_columns(bom: ParsedBOM, max_sample_rows: int = 5) -> str:
    """Format source columns with sample values for the prompt."""
    lines: list[str] = []
    for i, header in enumerate(bom.headers):
        # Gather sample values for this column
        samples: list[str] = []
        for row in bom.rows[:max_sample_rows]:
            val = row.get(header)
            if val is not None and str(val).strip():
                samples.append(str(val).strip()[:100])  # Cap long values

        sample_str = ", ".join(f'"{s}"' for s in samples[:5])
        lines.append(f'{i + 1}. "{header}"')
        if sample_str:
            lines.append(f"   Sample values: {sample_str}")
        else:
            lines.append("   Sample values: (all empty)")

    return "\n".join(lines)


def _render_prompt_template(template: str, **replacements: str) -> str:
    """Render only the known placeholders and leave literal JSON braces untouched."""
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def _format_learned_corrections(
    customer: str,
    feedback_store: object | None = None,
    *,
    max_examples: int = 8,
) -> str:
    """Render past human corrections for this customer as few-shot guidance.

    Closes the learning loop: corrections persisted via the feedback route are
    fed back into the mapping prompt so the model prefers the human-verified
    mapping for a customer it has seen before. Returns "" when there is nothing
    relevant (so the prompt is unchanged for new customers).
    """
    if not customer:
        return ""
    try:
        if feedback_store is None:
            from src.export.feedback_store import FeedbackStore

            feedback_store = FeedbackStore()
        corrections = feedback_store.load_corrections(customer)
    except (ImportError, OSError, ValueError) as exc:
        logger.warning("Could not load learned corrections: %s", exc)
        return ""

    relevant = [
        c
        for c in corrections
        if c.target_field and (c.raw_value or c.original_transformed)
    ]
    if not relevant:
        return ""

    lines = [
        "",
        "LEARNED CORRECTIONS — past human review for THIS customer. Treat these "
        "as strong evidence and prefer the corrected mapping when the same source "
        "value appears:",
    ]
    for c in relevant[-max_examples:]:  # most recent N
        source_value = c.raw_value or c.original_transformed or ""
        lines.append(
            f'- source value "{source_value}" → field "{c.target_field}" '
            f'= "{c.corrected_value}"'
        )
    return "\n".join(lines)


def build_mapping_prompt(
    bom: ParsedBOM,
    schema: TargetSchema,
    *,
    feedback_store: object | None = None,
) -> tuple[str, str]:
    """Build system + user prompt for column mapping.

    Returns (system_prompt, user_prompt).
    """
    template = _load_prompt_template()
    domain_context = _load_domain_context()

    # Fill the template
    prompt = _render_prompt_template(
        template,
        domain_context=domain_context,
        target_schema=schema.to_prompt_description(),
        source_columns=_format_source_columns(bom),
    )

    # Split: everything is the system prompt; user just says "map now"
    system_prompt = prompt

    # Close the learning loop: append customer-specific few-shot corrections.
    learned = _format_learned_corrections(bom.source.customer, feedback_store)
    if learned:
        system_prompt = f"{system_prompt}\n{learned}"

    user_prompt = (
        f"Map the {len(bom.headers)} source columns from the BOM file "
        f'"{bom.source.filename}" (customer: {bom.source.customer or "unknown"}) '
        f"to the target schema. Return the JSON mapping."
    )

    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# LLM mapper
# ---------------------------------------------------------------------------


async def map_columns(
    bom: ParsedBOM,
    llm: BaseLLM,
    schema: TargetSchema | None = None,
) -> MappingResult:
    """Map source BOM columns to target schema using LLM.

    Args:
        bom: Parsed BOM with headers and sample rows.
        llm: LLM client instance.
        schema: Target schema. Loaded from config if not provided.

    Returns:
        MappingResult with all column mappings and metadata.
    """
    if schema is None:
        schema = load_schema()

    system_prompt, user_prompt = build_mapping_prompt(bom, schema)

    logger.info(
        "Mapping %d columns for %s (customer: %s)",
        len(bom.headers),
        bom.source.filename,
        bom.source.customer,
    )

    response = await llm.complete(
        system=system_prompt,
        user=user_prompt,
        json_mode=True,
        temperature=0.0,
        max_tokens=4096,
    )

    logger.info(
        "LLM response: %d input tokens, %d output tokens, %.0fms",
        response.tokens_input,
        response.tokens_output,
        response.latency_ms,
    )

    # Parse the JSON response
    result = _parse_llm_response(response, bom, schema)

    # ARCH-005: independent second opinion on the column mapping. The primary
    # LLM call is the ONLY semantic judgement in the pipeline — Locks 2/3 verify
    # values, not column meaning, so a confidently wrong mapping produces
    # systematic wrong GREENs for a whole column. A cheap second call with a
    # different framing must AGREE before a column may carry GREEN; on
    # disagreement the confidence is capped below the 0.90 green bar (the
    # column stays YELLOW for review). Failure of the consensus call itself
    # changes nothing (fail-open to today's behaviour, logged).
    if _consensus_enabled():
        try:
            await _apply_mapping_consensus(result, bom, schema, llm)
        except Exception as exc:  # noqa: BLE001 — consensus must never kill a job
            logger.warning("Mapping consensus check failed (ignored): %s", exc)

    return result


_CONSENSUS_CONFIDENCE_CAP = 0.85

_CONSENSUS_SYSTEM = (
    "Du bist ein unabhängiger Prüfer für Spaltenzuordnungen in technischen "
    "Stücklisten. Du bekommst Quellspalten mit Beispielwerten und eine Liste "
    "erlaubter Zielfelder. Ordne jede Quellspalte GENAU EINEM Zielfeld zu oder "
    "null, wenn keines passt. Urteile nur anhand der Beispielwerte und des "
    "Spaltennamens. Antworte ausschließlich in JSON."
)


def _consensus_enabled() -> bool:
    mapping_cfg = load_app_config().get("mapping", {})
    return bool(mapping_cfg.get("enable_consensus", True))


def _build_consensus_prompt(bom: ParsedBOM, schema: TargetSchema) -> str:
    field_names = ", ".join(f'"{f.name}"' for f in schema.fields)
    return (
        "Erlaubte Zielfelder:\n"
        f"[{field_names}]\n\n"
        "Quellspalten mit Beispielwerten:\n"
        f"{_format_source_columns(bom)}\n\n"
        "Antwortformat (NUR JSON):\n"
        '{"assignments": {"<quellspalte>": "<zielfeld oder null>", ...}}'
    )


async def _apply_mapping_consensus(
    result: MappingResult,
    bom: ParsedBOM,
    schema: TargetSchema,
    llm: BaseLLM,
) -> None:
    response = await llm.complete(
        system=_CONSENSUS_SYSTEM,
        user=_build_consensus_prompt(bom, schema),
        json_mode=True,
        temperature=0.0,
        max_tokens=2048,
        use_mini=True,
    )

    try:
        payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        logger.warning("Consensus response unparseable (ignored): %s", exc)
        return

    assignments = payload.get("assignments")
    if not isinstance(assignments, dict):
        logger.warning("Consensus response missing 'assignments' (ignored)")
        return

    valid_fields = set(schema.field_names)
    disagreements = 0
    for mapping in result.mappings:
        if not mapping.target_field:
            continue
        secondary_raw = assignments.get(mapping.source_column)
        secondary = (
            str(secondary_raw).strip()
            if secondary_raw not in (None, "", "null")
            else None
        )
        if secondary is not None and secondary not in valid_fields:
            # Hallucinated field name — no usable second opinion for this column.
            continue
        if secondary == mapping.target_field:
            continue

        disagreements += 1
        mapping.confidence = min(mapping.confidence, _CONSENSUS_CONFIDENCE_CAP)
        mapping.candidate_confidence = min(
            mapping.candidate_confidence, _CONSENSUS_CONFIDENCE_CAP
        )
        mapping.reasoning = (
            (mapping.reasoning or "")
            + f" [KONSENS-ABWEICHUNG: Zweitprüfung sieht "
            + (f"'{secondary}'" if secondary else "keine Zuordnung")
            + "]"
        )

    if disagreements:
        logger.warning(
            "Mapping consensus: %d column(s) capped at %.2f (second opinion differs)",
            disagreements,
            _CONSENSUS_CONFIDENCE_CAP,
        )
        result.notes = (
            f"{result.notes} | " if result.notes else ""
        ) + f"Konsens-Prüfung: {disagreements} Spalte(n) ohne Übereinstimmung"
    else:
        logger.info("Mapping consensus: all mapped columns confirmed")


def _parse_llm_response(
    response: LLMResponse,
    bom: ParsedBOM,
    schema: TargetSchema,
) -> MappingResult:
    """Parse the LLM JSON response into a MappingResult."""
    try:
        data = json.loads(response.content)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM JSON response: %s", e)
        # Return empty mapping on parse failure
        return MappingResult(
            source_file=bom.source.filename,
            customer=bom.source.customer,
            mappings=[
                ColumnMapping(
                    source_column=h, confidence=0.0, reasoning="LLM JSON parse error"
                )
                for h in bom.headers
            ],
            unmapped_target_fields=schema.field_names,
            notes=f"JSON parse error: {e}",
            llm_response=response,
        )

    mappings: list[ColumnMapping] = []
    raw_mappings = data.get("mappings", [])

    # Build lookup of what the LLM returned
    for raw in raw_mappings:
        mappings.append(
            ColumnMapping(
                source_column=raw.get("source_column", ""),
                target_field=raw.get("target_field"),
                target_column=raw.get("target_column"),
                confidence=float(raw.get("confidence", 0.0)),
                reasoning=raw.get("reasoning", ""),
                candidate_confidence=float(
                    raw.get("candidate_confidence", raw.get("confidence", 0.0))
                ),
                candidate_reasoning=raw.get(
                    "candidate_reasoning", raw.get("reasoning", "")
                ),
            )
        )

    # Ensure every source header has a mapping entry (even if LLM missed some)
    mapped_sources = {m.source_column for m in mappings}
    for header in bom.headers:
        if header not in mapped_sources:
            mappings.append(
                ColumnMapping(
                    source_column=header,
                    target_field=None,
                    confidence=0.0,
                    reasoning="Not returned by LLM",
                    candidate_confidence=0.0,
                    candidate_reasoning="Not returned by LLM",
                )
            )

    return MappingResult(
        source_file=bom.source.filename,
        customer=bom.source.customer,
        mappings=mappings,
        unmapped_target_fields=data.get("unmapped_target_fields", []),
        notes=data.get("notes", ""),
        llm_response=response,
    )
