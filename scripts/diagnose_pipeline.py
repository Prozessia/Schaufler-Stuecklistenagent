"""Deep pipeline diagnosis for one BOM PDF.

Generates a Markdown report plus raw JSON artifacts that explain why cells do
or do not reach GREEN through parsing, mapping, transformation, scoring, and
the final GreenGate contract.

Usage examples:
    python scripts/diagnose_pipeline.py --pdf Mercedes
    python scripts/diagnose_pipeline.py --pdf data/input/PDF_POC/ZF/sample.pdf
    python scripts/diagnose_pipeline.py --pdf ZF --disable-counter-check
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from src.core.models import ParsedBOM, TransformationResult
from src.core.statuses import MatchResult
from src.ingestion.pdf_parser import _strip_llm_json_payload
from src.ingestion.structure_normalizer import parse_file
from src.llm.base import BaseLLM, LLMResponse
from src.mapping.llm_column_mapper import MappingResult, map_columns
from src.mapping.mapping_validator import ValidationResult, validate_mapping
from src.mapping.schema_registry import load_schema
from src.scoring.audit_trail import BomAuditTrail, CellAudit
from src.scoring.green_gate import GreenGateInput, can_be_green
from src.scoring.ensemble_scorer import score_bom_async
from src.scoring.threshold_manager import ScoringConfig, load_scoring_config
from src.scoring.vision_verifier import VisionCounterCheckService
from src.transform.cross_validator import CrossValidationResult, cross_validate
from src.transform.master_data_matcher import (
    get_coating_catalog,
    get_material_catalog,
    get_nitriding_catalog,
    get_parts_group_catalog,
)
from src.transform.pipeline import transform_bom

REPORT_ROOT = PROJECT_ROOT / "data" / "test_outputs" / "pipeline_diagnostics"


@dataclass(slots=True)
class LLMCallRecord:
    stage: str
    method: str
    json_mode: bool
    use_mini: bool
    max_tokens: int
    system_excerpt: str
    user_excerpt: str
    image_count: int
    response_model: str
    tokens_input: int
    tokens_output: int
    latency_ms: float
    content: str


class RecordingLLM(BaseLLM):
    """Transparent LLM proxy that records every request/response."""

    def __init__(self, inner: BaseLLM) -> None:
        self._inner = inner
        self.stage = "unknown"
        self.records: list[LLMCallRecord] = []

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        use_mini: bool = False,
    ) -> LLMResponse:
        response = await self._inner.complete(
            system,
            user,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
            use_mini=use_mini,
        )
        self.records.append(
            LLMCallRecord(
                stage=self.stage,
                method="complete",
                json_mode=json_mode,
                use_mini=use_mini,
                max_tokens=max_tokens,
                system_excerpt=_truncate(system, 220),
                user_excerpt=_truncate(user, 220),
                image_count=0,
                response_model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                latency_ms=response.latency_ms,
                content=response.content,
            )
        )
        return response

    async def complete_with_image(
        self,
        system: str,
        user: str,
        image_b64: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        response = await self._inner.complete_with_image(
            system,
            user,
            image_b64,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.records.append(
            LLMCallRecord(
                stage=self.stage,
                method="complete_with_image",
                json_mode=json_mode,
                use_mini=False,
                max_tokens=max_tokens,
                system_excerpt=_truncate(system, 220),
                user_excerpt=_truncate(user, 220),
                image_count=1,
                response_model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                latency_ms=response.latency_ms,
                content=response.content,
            )
        )
        return response

    async def complete_with_images(
        self,
        system: str,
        user: str,
        images_b64: list[str],
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        response = await self._inner.complete_with_images(
            system,
            user,
            images_b64,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.records.append(
            LLMCallRecord(
                stage=self.stage,
                method="complete_with_images",
                json_mode=json_mode,
                use_mini=False,
                max_tokens=max_tokens,
                system_excerpt=_truncate(system, 220),
                user_excerpt=_truncate(user, 220),
                image_count=len(images_b64),
                response_model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                latency_ms=response.latency_ms,
                content=response.content,
            )
        )
        return response


@dataclass(slots=True)
class GateDiagnosis:
    pre_gate_passed: bool
    final_gate_passed: bool
    counter_check_required: bool
    counter_check_passed: bool
    failure_stage: str
    failure_reasons: list[str]
    pre_gate_reasons: list[str]
    final_gate_reasons: list[str]


def _truncate(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = slug.strip("-._")
    return slug or "diagnostic"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


def _markdown_code_block(language: str, content: str) -> str:
    return f"```{language}\n{content.rstrip()}\n```"


def _artifact_path(base_dir: Path, name: str) -> Path:
    path = base_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _resolve_pdf_path(user_value: str) -> Path:
    candidate = Path(user_value)
    if candidate.exists() and candidate.is_file():
        return candidate.resolve()

    project_relative = (PROJECT_ROOT / user_value).resolve()
    if project_relative.exists() and project_relative.is_file():
        return project_relative

    search_root = PROJECT_ROOT / "data" / "input"
    wanted = user_value.casefold()
    matches = [
        path
        for path in search_root.rglob("*.pdf")
        if wanted in str(path).casefold() or wanted in path.name.casefold()
    ]

    if not matches:
        raise FileNotFoundError(f"No PDF found for selector: {user_value}")
    if len(matches) > 1:
        joined = "\n".join(f"- {path}" for path in matches[:20])
        raise RuntimeError(
            "PDF selector is ambiguous. Narrow it down. Matches:\n" + joined
        )
    return matches[0].resolve()


def _safe_raw_json_payload(content: str) -> tuple[str, Any | None, str | None]:
    sanitized = _strip_llm_json_payload(content or "")
    try:
        parsed = json.loads(sanitized)
        return sanitized, parsed, None
    except json.JSONDecodeError as exc:
        return sanitized, None, str(exc)


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _summarize_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return rows[:limit]


def _build_raw_flag_index(
    transform_result: TransformationResult,
) -> dict[tuple[int, str], list[str]]:
    raw_flags_by_cell: dict[tuple[int, str], list[str]] = defaultdict(list)
    for row_index, flags in transform_result.row_validation_flags.items():
        for flag in flags:
            payload = flag.split(":", 1)[1] if ":" in flag else ""
            column = payload.split(":", 1)[0].strip() if payload else ""
            if column:
                raw_flags_by_cell[(row_index, column)].append(flag)
    return raw_flags_by_cell


def _gate_diagnosis_for_cell(
    cell: CellAudit,
    transform_result: TransformationResult,
    config: ScoringConfig,
    *,
    counter_check_enabled: bool,
) -> GateDiagnosis:
    pre_gate_input = GreenGateInput(
        source_is_pdf=transform_result.source_is_pdf,
        extraction_method=transform_result.extraction_method,
        has_text_layer=transform_result.has_text_layer,
        vision_fallback_reason=transform_result.vision_fallback_reason,
        green_threshold=config.green_threshold,
        verify_green_threshold=config.verify_green_threshold,
        soft_green_floor=config.soft_green_floor,
        green_extraction_min_confidence=config.green_extraction_min_confidence,
        pdf_extracted_found=bool(cell.pdf_extracted_value),
        pdf_extraction_confidence=float(cell.pdf_extraction_confidence or 0.0),
        rule_score=float(cell.rule_score or 0.0),
        value_match_result=MatchResult(cell.value_match_result),
        value_match_detail=cell.value_match_detail,
        strict_exact_match=cell.value_match_detail.startswith("exact "),
        field_category=cell.field_category,
        check2_reason=cell.check2_reason,
        candidate_confidence=float(cell.candidate_confidence or 0.0),
        transform_method=cell.transform_method,
        transform_confidence=float(cell.transform_confidence or 0.0),
        counter_check_required=False,
        counter_check_passed=False,
        blocking_errors=list(cell.blocking_errors),
        hard_vetoes=list(cell.hard_vetoes),
    )

    pre_gate_passed, pre_gate_reasons = can_be_green(pre_gate_input)
    counter_check_required = pre_gate_passed and counter_check_enabled
    counter_check_passed = float(cell.counter_check_score or 0.0) >= 1.0

    final_gate_input = replace(
        pre_gate_input,
        counter_check_required=counter_check_required,
        counter_check_passed=counter_check_passed,
    )
    final_gate_passed, final_gate_reasons = can_be_green(final_gate_input)

    if not pre_gate_passed:
        return GateDiagnosis(
            pre_gate_passed=False,
            final_gate_passed=False,
            counter_check_required=False,
            counter_check_passed=False,
            failure_stage="pre_gate",
            failure_reasons=pre_gate_reasons,
            pre_gate_reasons=pre_gate_reasons,
            final_gate_reasons=final_gate_reasons,
        )

    if counter_check_required and not final_gate_passed:
        return GateDiagnosis(
            pre_gate_passed=True,
            final_gate_passed=False,
            counter_check_required=True,
            counter_check_passed=counter_check_passed,
            failure_stage="counter_check",
            failure_reasons=final_gate_reasons,
            pre_gate_reasons=pre_gate_reasons,
            final_gate_reasons=final_gate_reasons,
        )

    return GateDiagnosis(
        pre_gate_passed=pre_gate_passed,
        final_gate_passed=final_gate_passed,
        counter_check_required=counter_check_required,
        counter_check_passed=counter_check_passed,
        failure_stage="passed" if final_gate_passed else "pre_gate",
        failure_reasons=[] if final_gate_passed else final_gate_reasons,
        pre_gate_reasons=pre_gate_reasons,
        final_gate_reasons=final_gate_reasons,
    )


def _build_master_data_diagnostics(
    transform_result: TransformationResult,
) -> tuple[Counter, list[dict[str, Any]]]:
    catalogs = {
        "Material": get_material_catalog(),
        "Nitriding type": get_nitriding_catalog(),
        "Coating": get_coating_catalog(),
        "Parts Group": get_parts_group_catalog(),
    }
    counts: Counter = Counter()
    details: list[dict[str, Any]] = []

    for row in transform_result.rows:
        for cell in row.cells:
            catalog = catalogs.get(cell.target_field)
            if catalog is None:
                continue
            raw_value = str(cell.raw_value or "")
            match = catalog.match(raw_value)
            counts[f"{cell.target_field}:{match.method}"] += 1
            details.append(
                {
                    "row_index": row.row_index,
                    "target_field": cell.target_field,
                    "source_column": cell.source_column,
                    "raw_value": raw_value,
                    "transformed_value": cell.transformed_value,
                    "transform_method": cell.method,
                    "transform_confidence": cell.confidence,
                    "matcher_method": match.method,
                    "matcher_confidence": match.confidence,
                    "matcher_canonical": match.canonical,
                    "notes": cell.notes,
                }
            )

    return counts, details


def _format_counter(counter: Counter, *, limit: int = 20) -> str:
    if not counter:
        return "(none)"
    lines = []
    for key, value in counter.most_common(limit):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _format_mapping_summary(mapping: MappingResult) -> str:
    lines = [
        "target_field | source_column | confidence | reasoning",
        "--- | --- | ---: | ---",
    ]
    mapped = sorted(
        [item for item in mapping.mappings if item.target_field],
        key=lambda item: (item.target_field or "", item.source_column),
    )
    for item in mapped:
        lines.append(
            " | ".join(
                [
                    item.target_field or "",
                    item.source_column,
                    f"{item.confidence:.3f}",
                    _truncate(item.reasoning or "", 120).replace("|", "/"),
                ]
            )
        )
    return "\n".join(lines)


def _format_mapping_issues(mapping_validation: ValidationResult | None) -> str:
    if mapping_validation is None or not mapping_validation.issues:
        return "(none)"
    lines = []
    for issue in mapping_validation.issues:
        lines.append(
            "- "
            + f"[{issue.severity}] target={issue.target_field or '-'} "
            + f"source={issue.source_column or '-'} :: {issue.message}"
        )
    return "\n".join(lines)


def _format_cv_issues(cv_result: CrossValidationResult) -> str:
    if not cv_result.issues:
        return "(none)"
    lines = []
    for issue in cv_result.issues:
        lines.append(
            f"- [{issue.severity}] row={issue.row_index} field={issue.field} :: {issue.message}"
        )
    return "\n".join(lines)


def _non_green_detail_rows(
    audit: BomAuditTrail,
    gate_by_cell: dict[tuple[int, str], GateDiagnosis],
    raw_flags_by_cell: dict[tuple[int, str], list[str]],
    *,
    max_cells: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cell in audit.cells:
        if cell.classification.value == "green":
            continue
        gate = gate_by_cell[(cell.row_index, cell.target_field)]
        rows.append(
            {
                "row_index": cell.row_index,
                "target_field": cell.target_field,
                "source_column": cell.source_column,
                "classification": cell.classification.value,
                "final_status": cell.final_status,
                "failure_stage": gate.failure_stage,
                "failure_reasons": gate.failure_reasons,
                "candidate_confidence": cell.candidate_confidence,
                "transform_confidence": cell.transform_confidence,
                "rule_score": cell.rule_score,
                "check2_confidence": cell.pdf_extraction_confidence,
                "check2_reason": cell.check2_reason,
                "value_match_result": cell.value_match_result,
                "value_match_detail": cell.value_match_detail,
                "field_category": cell.field_category,
                "blocking_errors": cell.blocking_errors,
                "effective_hard_vetoes": cell.hard_vetoes,
                "raw_parser_flags": raw_flags_by_cell.get(
                    (cell.row_index, cell.source_column), []
                ),
                "counter_check_notes": cell.counter_check_notes,
                "counter_check_score": cell.counter_check_score,
                "transform_method": cell.transform_method,
                "raw_value": cell.raw_value,
                "transformed_value": cell.transformed_value,
                "reasoning": cell.reasoning,
            }
        )
    rows.sort(
        key=lambda item: (
            item["row_index"],
            item["classification"],
            item["target_field"],
        )
    )
    return rows[:max_cells]


def _format_non_green_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(none)"
    header = [
        "row",
        "target",
        "class",
        "stage",
        "fail_reason",
        "cand_conf",
        "rule",
        "check2",
        "match",
        "hard_vetoes",
        "raw_flags",
        "method",
    ]
    lines = ["\t".join(header)]
    for item in rows:
        lines.append(
            "\t".join(
                [
                    str(item["row_index"]),
                    str(item["target_field"]),
                    str(item["classification"]),
                    str(item["failure_stage"]),
                    ", ".join(item["failure_reasons"][:3]) or "-",
                    f"{float(item['candidate_confidence'] or 0.0):.3f}",
                    f"{float(item['rule_score'] or 0.0):.3f}",
                    f"{float(item['check2_confidence'] or 0.0):.3f}/{item['check2_reason']}",
                    f"{item['value_match_result']}:{_truncate(str(item['value_match_detail']), 32)}",
                    ",".join(item["effective_hard_vetoes"]) or "-",
                    ",".join(item["raw_parser_flags"]) or "-",
                    str(item["transform_method"]),
                ]
            )
        )
    return _markdown_code_block("text", "\n".join(lines))


def _build_report(
    *,
    pdf_path: Path,
    output_dir: Path,
    bom: ParsedBOM,
    mapping: MappingResult,
    mapping_validation: ValidationResult | None,
    transform_result: TransformationResult,
    cv_result: CrossValidationResult,
    audit: BomAuditTrail,
    config: ScoringConfig,
    llm_records: list[LLMCallRecord],
    parser_artifacts: list[Path],
    mapping_artifacts: list[Path],
    raw_flags_by_cell: dict[tuple[int, str], list[str]],
    gate_by_cell: dict[tuple[int, str], GateDiagnosis],
    master_data_counts: Counter,
    master_data_details: list[dict[str, Any]],
    counter_check_enabled_for_run: bool,
    max_detail_cells: int,
) -> str:
    parser_records = [record for record in llm_records if record.stage == "parser"]
    mapping_records = [record for record in llm_records if record.stage == "mapping"]
    scoring_records = [record for record in llm_records if record.stage == "scoring"]

    gate_failures = Counter()
    hard_vetoes = Counter()
    blocking_errors = Counter()
    check2_reasons = Counter()
    value_match_results = Counter()
    coord_vetos_effective = 0
    coord_flags_present = 0

    for cell in audit.cells:
        gate = gate_by_cell[(cell.row_index, cell.target_field)]
        if gate.failure_reasons:
            gate_failures[gate.failure_reasons[0]] += 1
        for veto in cell.hard_vetoes:
            hard_vetoes[veto] += 1
            if veto.startswith("PDF_COORD"):
                coord_vetos_effective += 1
        for error in cell.blocking_errors:
            blocking_errors[error] += 1
        check2_reasons[cell.check2_reason or "(empty)"] += 1
        value_match_results[cell.value_match_result] += 1
        if raw_flags_by_cell.get((cell.row_index, cell.source_column)):
            coord_flags_present += 1

    parser_flag_counts = Counter()
    for flags in transform_result.row_validation_flags.values():
        for flag in flags:
            parser_flag_counts[flag.split(":", 1)[0]] += 1

    raw_parser_preview = []
    for idx, record in enumerate(parser_records, start=1):
        raw_parser_preview.append(
            {
                "index": idx,
                "method": record.method,
                "model": record.response_model,
                "use_mini": record.use_mini,
                "tokens_input": record.tokens_input,
                "tokens_output": record.tokens_output,
                "latency_ms": round(record.latency_ms, 1),
                "artifact": (
                    str(parser_artifacts[idx - 1])
                    if idx - 1 < len(parser_artifacts)
                    else ""
                ),
            }
        )

    non_green_rows = _non_green_detail_rows(
        audit,
        gate_by_cell,
        raw_flags_by_cell,
        max_cells=max_detail_cells,
    )

    lines: list[str] = []
    lines.append(f"# Diagnose Report: {pdf_path.name}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- Source file: `{pdf_path}`")
    lines.append(f"- Report directory: `{output_dir}`")
    lines.append(f"- Extraction method: `{bom.source.extraction_method}`")
    lines.append(f"- Has text layer: `{transform_result.has_text_layer}`")
    lines.append(f"- Parser confidence: `{bom.source.extraction_confidence:.3f}`")
    lines.append(f"- Rows parsed: `{bom.total_rows}` | Headers: `{bom.total_columns}`")
    lines.append(
        f"- Audit summary: GREEN `{audit.green_count}`, YELLOW `{audit.yellow_count}`, RED `{audit.red_count}`, NEUTRAL `{audit.neutral_count}`"
    )
    lines.append(
        f"- Top GreenGate blockers: `{', '.join(f'{k}={v}' for k, v in gate_failures.most_common(5)) or 'none'}`"
    )
    lines.append(
        f"- Legacy geometry signal check: raw parser flags on `{coord_flags_present}` cells, effective coordinate vetoes on `{coord_vetos_effective}` cells"
    )
    lines.append("")

    lines.append("## Layer 1: Parser Output")
    lines.append("")
    lines.append(f"- Parser LLM calls: `{len(parser_records)}`")
    lines.append(f"- Mapping LLM calls: `{len(mapping_records)}`")
    lines.append(f"- Scoring/counter-check LLM calls: `{len(scoring_records)}`")
    lines.append(
        f"- `ParsedBOM.metadata.check2_reason`: `{bom.metadata.get('check2_reason', '')}`"
    )
    lines.append(
        f"- `ParsedBOM.metadata.llm_json_repaired`: `{bom.metadata.get('llm_json_repaired', False)}`"
    )
    lines.append(
        f"- `ParsedBOM.metadata.llm_uncertain_cells`: `{sum(len(v) for v in bom.metadata.get('llm_uncertain_cells', {}).values())}` markers"
    )
    lines.append("")
    lines.append("### Raw LLM Parser Responses")
    lines.append("")
    if raw_parser_preview:
        lines.append(_markdown_code_block("json", _json_dump(raw_parser_preview)))
    else:
        lines.append("No parser LLM calls were captured.")
    lines.append("")
    if parser_artifacts:
        lines.append("Raw parser artifacts:")
        lines.extend(f"- `{path}`" for path in parser_artifacts)
        lines.append("")

    lines.append("### ParsedBOM Snapshot")
    lines.append("")
    lines.append("Headers:")
    lines.append(_markdown_code_block("json", _json_dump(bom.headers)))
    lines.append("")
    lines.append("Sample rows:")
    lines.append(_markdown_code_block("json", _json_dump(_summarize_rows(bom.rows, 5))))
    lines.append("")
    lines.append("Row validation flag prefixes:")
    lines.append(_format_counter(parser_flag_counts, limit=20))
    lines.append("")

    lines.append("## Layer 2: Mapping, Matcher, Scorer")
    lines.append("")
    lines.append(
        f"- Mapping summary: `{mapping.mapped_count}` mapped source columns, average confidence `{mapping.avg_confidence:.3f}`"
    )
    lines.append(
        f"- Cross-validation: `{cv_result.error_count}` errors, `{cv_result.warning_count}` warnings, `{cv_result.info_count}` info"
    )
    lines.append("")
    lines.append("### Column Mapping")
    lines.append("")
    lines.append(_format_mapping_summary(mapping))
    lines.append("")
    lines.append("### Mapping Validator Findings")
    lines.append("")
    lines.append(_format_mapping_issues(mapping_validation))
    lines.append("")
    lines.append("### Cross-Validation Findings")
    lines.append("")
    lines.append(_format_cv_issues(cv_result))
    lines.append("")
    lines.append("### Master-Data Matcher Summary")
    lines.append("")
    lines.append(_format_counter(master_data_counts, limit=50))
    lines.append("")
    lines.append("Master-data detail sample:")
    lines.append(
        _markdown_code_block(
            "json",
            _json_dump(master_data_details[: min(len(master_data_details), 40)]),
        )
    )
    lines.append("")
    lines.append("### Scoring Signal Summary")
    lines.append("")
    lines.append(f"- Value match results: `{dict(value_match_results)}`")
    lines.append(f"- Blocking errors: `{dict(blocking_errors)}`")
    lines.append(f"- Effective hard vetoes: `{dict(hard_vetoes)}`")
    lines.append(f"- Check2 reasons: `{dict(check2_reasons)}`")
    lines.append("")

    lines.append("## Layer 3: GreenGate Failure Isolation")
    lines.append("")
    lines.append(f"- Config green threshold: `{config.green_threshold:.2f}`")
    lines.append(f"- Config verify threshold: `{config.verify_green_threshold:.2f}`")
    lines.append(
        f"- Counter-check enabled for this run: `{counter_check_enabled_for_run}`"
    )
    lines.append("")
    lines.append("Top exact GreenGate failure reasons:")
    lines.append(_format_counter(gate_failures, limit=30))
    lines.append("")
    lines.append(
        "Interpretation of legacy image checks on the text path: raw parser flags show whether "
        "old coordinate signals are still present upstream; effective hard vetoes show whether they still "
        "survive GreenGate/scorer filtering in the final decision."
    )
    lines.append("")
    lines.append("### Detailed Non-Green Cells")
    lines.append("")
    lines.append(
        f"Showing up to `{max_detail_cells}` non-green cells. The full JSON artifact is stored beside this report."
    )
    lines.append("")
    lines.append(_format_non_green_table(non_green_rows))
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append("- Parsed BOM JSON: `parsed_bom.json`")
    lines.append("- Transform result JSON: `transform_result.json`")
    lines.append("- Audit trail JSON: `audit_trail.json`")
    lines.append("- Non-green cells JSON: `non_green_cells.json`")
    lines.append("- LLM call log JSON: `llm_calls.json`")
    for path in mapping_artifacts:
        lines.append(f"- Mapping raw response: `{path.name}`")
    for path in parser_artifacts:
        lines.append(f"- Parser raw response: `{path.name}`")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


async def _run(args: argparse.Namespace) -> Path:
    pdf_path = _resolve_pdf_path(args.pdf)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = REPORT_ROOT / f"{_slugify(pdf_path.stem)}_{run_stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from src.llm.azure_openai import AzureOpenAILLM

        llm_impl = AzureOpenAILLM()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Could not initialize AzureOpenAILLM. This diagnosis needs the live parser path."
        ) from exc

    recording_llm = RecordingLLM(llm_impl)
    counter_check_service: VisionCounterCheckService | None = None
    schema = load_schema()
    config = load_scoring_config()

    try:
        recording_llm.stage = "parser"
        bom = await parse_file(pdf_path, llm=recording_llm)

        recording_llm.stage = "mapping"
        mapping = await map_columns(bom, recording_llm, schema)

        mapping_validation = validate_mapping(mapping, bom, schema)
        mapping = mapping.model_copy(
            update={"mappings": list(mapping_validation.adjusted_mappings)}
        )

        transform_result = transform_bom(bom, mapping, schema)
        cv_result = cross_validate(transform_result)

        if not args.disable_counter_check:
            counter_check_service = VisionCounterCheckService(recording_llm)

        recording_llm.stage = "scoring"
        audit = await score_bom_async(
            transform_result,
            mapping,
            cv_result=cv_result,
            schema=schema,
            config=config,
            mapping_validation=mapping_validation,
            counter_check_service=counter_check_service,
            job_id=f"diagnose-{run_stamp}",
            pdf_path=pdf_path,
        )
    finally:
        if counter_check_service is not None:
            counter_check_service.close()

    parser_artifacts: list[Path] = []
    mapping_artifacts: list[Path] = []
    llm_call_log: list[dict[str, Any]] = []

    for index, record in enumerate(recording_llm.records, start=1):
        sanitized_payload, parsed_payload, parse_error = _safe_raw_json_payload(
            record.content
        )
        record_payload = {
            "index": index,
            "stage": record.stage,
            "method": record.method,
            "json_mode": record.json_mode,
            "use_mini": record.use_mini,
            "max_tokens": record.max_tokens,
            "response_model": record.response_model,
            "tokens_input": record.tokens_input,
            "tokens_output": record.tokens_output,
            "latency_ms": record.latency_ms,
            "system_excerpt": record.system_excerpt,
            "user_excerpt": record.user_excerpt,
            "image_count": record.image_count,
            "raw_response_artifact": "",
            "json_parse_error": parse_error,
        }

        if record.stage == "parser":
            suffix = "json" if parsed_payload is not None else "txt"
            artifact = _artifact_path(
                output_dir, f"parser_call_{index:02d}_raw.{suffix}"
            )
            _write_text(
                artifact,
                (
                    _json_dump(parsed_payload)
                    if parsed_payload is not None
                    else sanitized_payload
                ),
            )
            parser_artifacts.append(artifact)
            record_payload["raw_response_artifact"] = artifact.name
        elif record.stage == "mapping":
            suffix = "json" if parsed_payload is not None else "txt"
            artifact = _artifact_path(
                output_dir, f"mapping_call_{index:02d}_raw.{suffix}"
            )
            _write_text(
                artifact,
                (
                    _json_dump(parsed_payload)
                    if parsed_payload is not None
                    else sanitized_payload
                ),
            )
            mapping_artifacts.append(artifact)
            record_payload["raw_response_artifact"] = artifact.name

        llm_call_log.append(record_payload)

    raw_flags_by_cell = _build_raw_flag_index(transform_result)
    counter_check_enabled = (
        config.enable_counter_check
        and counter_check_service is not None
        and transform_result.source_is_pdf
    )
    gate_by_cell = {
        (cell.row_index, cell.target_field): _gate_diagnosis_for_cell(
            cell,
            transform_result,
            config,
            counter_check_enabled=counter_check_enabled,
        )
        for cell in audit.cells
    }
    master_data_counts, master_data_details = _build_master_data_diagnostics(
        transform_result
    )
    non_green_cells = _non_green_detail_rows(
        audit,
        gate_by_cell,
        raw_flags_by_cell,
        max_cells=10**9,
    )

    _write_text(
        _artifact_path(output_dir, "parsed_bom.json"),
        _json_dump(_model_dump(bom)),
    )
    _write_text(
        _artifact_path(output_dir, "transform_result.json"),
        _json_dump(_model_dump(transform_result)),
    )
    _write_text(
        _artifact_path(output_dir, "audit_trail.json"),
        _json_dump(_model_dump(audit)),
    )
    _write_text(
        _artifact_path(output_dir, "non_green_cells.json"),
        _json_dump(non_green_cells),
    )
    _write_text(
        _artifact_path(output_dir, "llm_calls.json"),
        _json_dump(llm_call_log),
    )
    _write_text(
        _artifact_path(output_dir, "master_data_details.json"),
        _json_dump(master_data_details),
    )

    report = _build_report(
        pdf_path=pdf_path,
        output_dir=output_dir,
        bom=bom,
        mapping=mapping,
        mapping_validation=mapping_validation,
        transform_result=transform_result,
        cv_result=cv_result,
        audit=audit,
        config=config,
        llm_records=recording_llm.records,
        parser_artifacts=parser_artifacts,
        mapping_artifacts=mapping_artifacts,
        raw_flags_by_cell=raw_flags_by_cell,
        gate_by_cell=gate_by_cell,
        master_data_counts=master_data_counts,
        master_data_details=master_data_details,
        counter_check_enabled_for_run=counter_check_enabled,
        max_detail_cells=args.max_detail_cells,
    )
    report_path = _artifact_path(output_dir, "report.md")
    _write_text(report_path, report)
    return report_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a deep Markdown diagnosis for one BOM PDF.",
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Absolute path or case-insensitive substring that identifies one PDF.",
    )
    parser.add_argument(
        "--max-detail-cells",
        type=int,
        default=400,
        help="Maximum number of non-green cells to inline into the Markdown report.",
    )
    parser.add_argument(
        "--disable-counter-check",
        action="store_true",
        help="Skip CHECK5 counter-check calls and diagnose only the deterministic pre-gate.",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    report_path = asyncio.run(_run(args))
    print(f"Diagnosis report written to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
