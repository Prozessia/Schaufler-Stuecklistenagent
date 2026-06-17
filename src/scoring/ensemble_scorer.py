"""Strict triple-check scorer (ZERO FALSE POSITIVE contract).

GREEN is assigned only through `can_be_green()` with this hard gate.
The scorer mirrors that decision contract so text-path PDFs do not get
re-penalized by image-world vetoes after the gate has already filtered them.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.core.models import (
    CellTransformation,
    ExtractionMethod,
    SourceLocation,
    TransformationResult,
    TransformedRow,
)
from src.core.positions import POSITION_FIELDS as _POSITION_FIELDS
from src.core.positions import normalize_position as _normalize_position_value
from src.core.statuses import FinalStatus, MatchResult
from src.mapping.llm_column_mapper import MappingResult
from src.mapping.mapping_validator import ValidationResult
from src.mapping.schema_registry import TargetSchema, load_schema
from src.scoring.audit_trail import BomAuditTrail, CellAudit, MappingValidationFinding
from src.scoring.green_gate import (
    GreenGateInput,
    can_be_green,
    effective_hard_vetoes_for,
    text_path_transform_verified,
)
from src.scoring.pdf_value_extractor import PDFValueExtractor
from src.scoring.threshold_manager import (
    ScoringConfig,
    TrafficLight,
    load_scoring_config,
)
from src.scoring.value_comparator import ValueComparator
from src.scoring.vision_verifier import BatchFieldRequest, VisionCounterCheckService
from src.transform.cross_validator import CrossValidationResult

logger = logging.getLogger(__name__)

_ERP_MISSING_POSITION_REASON = (
    "Critical Error: Position exists on PDF drawing/part list, but is completely "
    "missing in ERP master data (Excel)"
)
_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# B1: default position patterns (priority order — specific patterns only).
# Mirrors config/pos_patterns.yaml; used as fallback when the file is absent.
# The bare sequential pattern (\b\d{1,4}\b) is intentionally NOT a default: on a
# raw text layer it has no column context and captures every number as a phantom
# position. Sequential positions are recovered from the extracted position column
# by the B2 reconciler instead. The pattern can be enabled per instance via YAML.
_DEFAULT_POSITION_PATTERNS = [
    r"\d+-\d+",
    r"K-\d+",
    r"[A-Z]-\d+",
]


def _load_position_patterns() -> re.Pattern:
    """Load position-identifier patterns from YAML (fallback to defaults).

    Patterns are joined into a single named group in priority order. Compiled
    once at module import — not per call.
    """
    patterns = _DEFAULT_POSITION_PATTERNS
    path = _CONFIG_DIR / "pos_patterns.yaml"
    if path.exists():
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            loaded = data.get("position_patterns")
            if isinstance(loaded, list):
                cleaned = [str(p) for p in loaded if str(p).strip()]
                if cleaned:
                    patterns = cleaned
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not load pos_patterns.yaml (%s); using defaults", exc
            )

    alternation = "|".join(patterns)
    return re.compile(rf"(?:\b|\s)(?P<pos>{alternation})\b", re.IGNORECASE)


_PDF_POSITION_RE = _load_position_patterns()
_PDF_CUSTOMER_PART_RE = re.compile(
    r"\b(?:[A-Z][A-Z0-9._/-]*\d[A-Z0-9._/-]*|\d[A-Z0-9._/-]*[A-Z][A-Z0-9._/-]*)\b",
    re.IGNORECASE,
)
_PDF_QUANTITY_RE = re.compile(
    r"(?<![A-Z0-9])[-+]?\d+(?:[.,]\d+)?(?:\s*(?:stk|stck|pcs|pc|ea|x))?(?![A-Z0-9])",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _DeferredCounterCheck:
    """A GREEN candidate awaiting the page-batched counter-check (PERF-002)."""

    audit_index: int
    page_number: int
    gate_input: GreenGateInput
    target_field: str
    source_column: str
    primary_value: str | None
    bbox_hint: str = "unknown"


@dataclass(slots=True)
class _PDFPositionEvidence:
    position: str
    line_text: str
    page_index: int
    line_index: int
    customer_part_number: str | None = None
    design_count: str | None = None
    description: str | None = None


def score_bom(
    transform_result: TransformationResult,
    mapping: MappingResult,
    cv_result: CrossValidationResult | None = None,
    schema: TargetSchema | None = None,
    config: ScoringConfig | None = None,
    mapping_validation: ValidationResult | None = None,
    counter_check_service: VisionCounterCheckService | None = None,
    job_id: str | None = None,
    pdf_path: Path | None = None,
) -> BomAuditTrail:
    """Synchronous scorer wrapper for scripts/tests.

    Use ``await score_bom_async(...)`` from async contexts.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            score_bom_async(
                transform_result,
                mapping,
                cv_result=cv_result,
                schema=schema,
                config=config,
                mapping_validation=mapping_validation,
                counter_check_service=counter_check_service,
                job_id=job_id,
                pdf_path=pdf_path,
            )
        )

    raise RuntimeError(
        "score_bom() cannot be called from an active event loop; "
        "use 'await score_bom_async(...)'."
    )


async def score_bom_async(
    transform_result: TransformationResult,
    mapping: MappingResult,
    cv_result: CrossValidationResult | None = None,
    schema: TargetSchema | None = None,
    config: ScoringConfig | None = None,
    mapping_validation: ValidationResult | None = None,
    counter_check_service: VisionCounterCheckService | None = None,
    job_id: str | None = None,
    pdf_path: Path | None = None,
) -> BomAuditTrail:
    """Score every transformed cell using strict triple-check logic."""
    if schema is None:
        schema = load_schema()
    if config is None:
        config = load_scoring_config()

    mapping_conf = _build_mapping_confidence_map(mapping)
    mapping_reason = _build_mapping_reasoning_map(mapping)

    field_types = {field.name: field for field in schema.fields}
    cv_issues = _index_cv_issues(cv_result) if cv_result else {}
    cv_contradictions = _index_cv_contradictions(cv_result) if cv_result else set()

    mv_error_fields, mv_warning_fields = _index_mapping_validation(mapping_validation)
    coord_mismatch_cells, coord_column_conflict_cells, dual_mismatch_cells, plaus_cells = (
        _index_row_validation_flags(transform_result)
    )

    extractor = PDFValueExtractor()
    comparator = ValueComparator()
    counter_check_enabled = (
        config.enable_counter_check
        and counter_check_service is not None
        and transform_result.source_is_pdf
        and bool(job_id)
        and pdf_path is not None
    )

    audit = BomAuditTrail(
        source_file=transform_result.source_file,
        customer=transform_result.customer,
        mapping_validation_issues=_serialize_mapping_validation(mapping_validation),
        expected_position_count=transform_result.expected_position_count,
        expected_position_ids=list(transform_result.expected_position_ids),
        expected_row_keys=list(transform_result.expected_row_keys),
        guard_basis=transform_result.guard_basis,
        non_data_row_flags={
            int(idx): list(reasons)
            for idx, reasons in (transform_result.non_data_row_flags or {}).items()
        },
    )

    deferred_counter_checks: list[_DeferredCounterCheck] = []

    for row in transform_result.rows:
        for cell in row.cells:
            field_def = field_types.get(cell.target_field)
            is_required = bool(field_def.required) if field_def else False
            source_location = _resolve_source_location(transform_result, row, cell)

            transformed_val = (cell.transformed_value or "").strip()
            candidate_conf = _clamp(mapping_conf.get(cell.target_field, 0.0))
            candidate_reason = mapping_reason.get(cell.target_field, "")

            # B2/B3: reconciler-synthesised MISSING positions are hard-vetoed to
            # RED — they exist in the PDF but were never extracted. No GREEN path.
            if cell.method == "synthetic_pdf_only_missing":
                audit.cells = [
                    *audit.cells,
                    _make_missing_red_cell(
                        cell.transformed_value or "",
                        cell.target_field,
                        cell.target_column,
                        row.row_index,
                    ),
                ]
                audit.red_count += 1
                audit.total_scored += 1
                continue

            if cell.method == "empty" or not transformed_val:
                _score_empty_cell(
                    audit=audit,
                    row=row,
                    cell=cell,
                    is_required=is_required,
                    source_location=source_location,
                    candidate_conf=candidate_conf,
                    candidate_reason=candidate_reason,
                    has_blocking_validator_error=(cell.target_field in mv_error_fields),
                    empty_non_required_as_neutral=config.empty_non_required_as_neutral,
                    empty_non_required_as_yellow=config.empty_non_required_as_yellow,
                )
                continue

            cv_severity = cv_issues.get((row.row_index, cell.target_field))
            has_contradiction = (row.row_index, cell.target_field) in cv_contradictions

            rule_score, rule_details = _compute_rule_score(
                cell=cell,
                field_def=field_def,
                cv_severity=cv_severity,
            )

            extraction = extractor.extract_value_for_column(
                source_column_name=cell.source_column,
                source_row_index=row.row_index,
                pdf_document=transform_result,
                target_field=cell.target_field,
                mapped_value=transformed_val,
            )

            compare_extraction_confidence = extraction.confidence
            if (
                extraction.reason == "no_coordinate_match"
                and transform_result.has_text_layer
                and transform_result.source_extraction_confidence > 0.0
            ):
                compare_extraction_confidence = max(
                    compare_extraction_confidence,
                    transform_result.source_extraction_confidence,
                )

            compare_result = comparator.compare_values(
                mapped_value=transformed_val,
                extracted_value=extraction.extracted_value,
                target_field=cell.target_field,
                extraction_confidence=compare_extraction_confidence,
                extraction_reason=extraction.reason,
                document_text_layer=transform_result.document_text_layer,
                extraction_match_type=extraction.match_type,
            )

            check2_found = extraction.found
            check2_confidence = extraction.confidence
            check2_reason = extraction.reason
            check2_extracted_value = extraction.extracted_value
            check2_source_location = extraction.source_location
            if (
                compare_result.detail
                == "customer part number verified via global pdf text layer"
            ):
                check2_found = True
                check2_confidence = max(
                    compare_extraction_confidence,
                    transform_result.source_extraction_confidence,
                )
                check2_reason = "global_pdf_text_layer"
                check2_extracted_value = extraction.extracted_value or transformed_val
                check2_source_location = (
                    extraction.source_location or "document_text_layer"
                )

            blocking_errors = []
            if cell.target_field in mv_error_fields:
                blocking_errors.append("MAPPING_VALIDATOR_ERROR")

            hard_vetoes: list[str] = []
            if compare_result.result == MatchResult.MISMATCH:
                hard_vetoes.append("CHECK3_VALUE_MISMATCH")
            if (row.row_index, cell.source_column) in coord_mismatch_cells:
                hard_vetoes.append("PDF_COORDINATE_MISMATCH")
            if (row.row_index, cell.source_column) in coord_column_conflict_cells:
                hard_vetoes.append("PDF_COLUMN_CONFLICT")
            if (row.row_index, cell.source_column) in dual_mismatch_cells:
                hard_vetoes.append("PDF_DUAL_MISMATCH")
            if cv_severity == "error":
                hard_vetoes.append("CROSS_VALIDATION_ERROR")
            if has_contradiction:
                hard_vetoes.append("ENGINEERING_CONTRADICTION")

            if compare_result.field_category == "A" and candidate_conf < 0.55:
                hard_vetoes.append("LOW_MAPPING_CONFIDENCE_CATEGORY_A")

            soft_score = _clamp((cell.confidence + candidate_conf + rule_score) / 3.0)

            gate_input = GreenGateInput(
                source_is_pdf=transform_result.source_is_pdf,
                extraction_method=transform_result.extraction_method,
                has_text_layer=transform_result.has_text_layer,
                vision_fallback_reason=transform_result.vision_fallback_reason,
                green_threshold=config.green_threshold,
                verify_green_threshold=config.verify_green_threshold,
                soft_green_floor=config.soft_green_floor,
                green_extraction_min_confidence=config.green_extraction_min_confidence,
                pdf_extracted_found=check2_found,
                pdf_extraction_confidence=check2_confidence,
                check2_reason=check2_reason,
                rule_score=rule_score,
                value_match_result=compare_result.result,
                value_match_detail=compare_result.detail,
                strict_exact_match=compare_result.strict_exact_match,
                field_category=compare_result.field_category,
                candidate_confidence=candidate_conf,
                transform_method=cell.method,
                transform_confidence=cell.confidence,
                blocking_errors=blocking_errors,
                hard_vetoes=hard_vetoes,
                value_plausible=_is_value_plausible(cell.target_field, transformed_val),
                plausibility_field=cell.target_field,
                plausibility_value=transformed_val,
            )

            pre_gate_input = replace(
                gate_input,
                counter_check_required=False,
                counter_check_passed=False,
            )
            effective_hard_vetoes = effective_hard_vetoes_for(pre_gate_input)
            pre_gate_passed, pre_gate_evidence = can_be_green(pre_gate_input)

            counter_check_score: float | None = None
            counter_check_notes = "counter_check_skipped_pre_gate"
            deferred_page_number: int | None = None

            # ARCH-002: scan-path YELLOW recheck candidacy. A scanned PDF can only
            # reach GREEN via the verified-scan gate, which REQUIRES a passed
            # counter-check — but the pre-gate (counter_check_required=False) can
            # never pass on scans, so without this the counter-check never ran and
            # scan-GREEN was structurally dead. Candidacy mirrors every
            # verified-scan precondition except the counter-check itself.
            scan_recheck_candidate = (
                config.enable_yellow_recheck
                and counter_check_enabled
                and not pre_gate_passed
                and transform_result.extraction_method == ExtractionMethod.GPT4O_VISION
                and not transform_result.has_text_layer
                and not transform_result.vision_fallback_reason
                and not blocking_errors
                and not effective_hard_vetoes
                and rule_score >= config.verify_green_threshold
                and gate_input.value_plausible
                and bool(transformed_val)
            )

            if (pre_gate_passed and counter_check_enabled) or scan_recheck_candidate:
                # PERF-002: defer — verified in ONE Vision call per page after the
                # loop, instead of one call per cell. Until then the cell stays
                # YELLOW; promotion happens only through the full gate.
                counter_check_notes = "counter_check_missing_page_number"
                counter_check_score = 0.0
                deferred_page_number = _resolve_counter_check_page_number(
                    source_location,
                    extraction.source_location,
                )
                if deferred_page_number is not None:
                    counter_check_notes = "counter_check_deferred_batch"
                is_green = False
                green_evidence = []
            elif pre_gate_passed:
                counter_check_notes = "counter_check_not_enabled"
                is_green, green_evidence = can_be_green(pre_gate_input)
            else:
                is_green = False
                green_evidence = pre_gate_evidence

            classification = TrafficLight.YELLOW
            final_status = FinalStatus.YELLOW

            if config.soft_vetoes_as_yellow:
                soft_only_vetoes = {
                    "PDF_COORDINATE_MISMATCH",
                    "PDF_COLUMN_CONFLICT",
                    "LOW_MAPPING_CONFIDENCE_CATEGORY_A",
                }
                effective_hard_vetoes = [
                    veto
                    for veto in effective_hard_vetoes
                    if veto not in soft_only_vetoes
                ]

            if is_green:
                classification = TrafficLight.GREEN
                final_status = FinalStatus.GREEN
            elif effective_hard_vetoes:
                classification = TrafficLight.RED
                final_status = FinalStatus.RED
            elif blocking_errors:
                # A MAPPING_VALIDATOR_ERROR flags the whole TARGET COLUMN, not the
                # individual value. It must withhold GREEN — but it must not force a
                # cell that carries a usable, extracted value to RED (that turned
                # clean, value-matched cells like Design Count="4" into 114/114 RED).
                # Cap such cells at YELLOW (review) instead. GREEN is already
                # impossible here (can_be_green hard-blocks on blocking_errors), so
                # the zero-false-green guarantee is untouched. A cell without a value
                # still falls through to RED.
                only_mapping_validator = all(
                    b == "MAPPING_VALIDATOR_ERROR" for b in blocking_errors
                )
                if only_mapping_validator and (cell.transformed_value or "").strip():
                    classification = TrafficLight.YELLOW
                    final_status = FinalStatus.YELLOW
                    rule_details.append("MAPPING_VALIDATOR_ERROR_CAP")
                else:
                    classification = TrafficLight.RED
                    final_status = FinalStatus.RED
            elif compare_result.result == MatchResult.NOT_APPLICABLE:
                classification = TrafficLight.YELLOW
                final_status = FinalStatus.YELLOW
            elif compare_result.result == MatchResult.UNCERTAIN:
                classification = TrafficLight.YELLOW
                final_status = FinalStatus.YELLOW

            if (
                cell.target_field in mv_warning_fields
                and classification == TrafficLight.GREEN
            ):
                # A warning cannot force GREEN; cap it at YELLOW.
                classification = TrafficLight.YELLOW
                final_status = FinalStatus.YELLOW
                green_evidence = []
                rule_details.append("MAPPING_VALIDATOR_WARNING_CAP")

            if (
                (row.row_index, cell.source_column) in plaus_cells
                and classification == TrafficLight.GREEN
            ):
                # A plausibility flag (PLAUS: soft veto) caps GREEN at YELLOW.
                classification = TrafficLight.YELLOW
                final_status = FinalStatus.YELLOW
                green_evidence = []
                rule_details.append("PLAUSIBILITY_FLAG_CAP")

            verify_score = _compute_verify_score(
                gate_input=pre_gate_input,
                compare_result=compare_result,
                is_green=is_green,
                effective_hard_vetoes=effective_hard_vetoes,
            )
            final_score = _compute_final_score(classification, soft_score, verify_score)

            if cell.target_field in mv_warning_fields:
                rule_details.append("mapping validator warning")

            reasoning = _build_reasoning(
                classification=classification,
                compare_result=compare_result,
                extraction_confidence=check2_confidence,
                extraction_reason=check2_reason,
                candidate_conf=candidate_conf,
                rule_score=rule_score,
                hard_vetoes=effective_hard_vetoes,
                blocking_errors=blocking_errors,
                green_evidence=green_evidence,
            )

            cell_audit = CellAudit(
                row_index=row.row_index,
                target_field=cell.target_field,
                target_column=cell.target_column,
                source_column=cell.source_column,
                raw_value=cell.raw_value,
                transformed_value=cell.transformed_value,
                transform_method=cell.method,
                source_location=source_location,
                transform_confidence=round(_clamp(cell.confidence), 4),
                mapping_confidence=round(candidate_conf, 4),
                candidate_confidence=round(candidate_conf, 4),
                candidate_reasoning=candidate_reason,
                pdf_extracted_value=check2_extracted_value,
                pdf_extraction_confidence=round(check2_confidence, 4),
                check2_reason=check2_reason,
                pdf_source_location=check2_source_location,
                value_match_result=compare_result.result.value,
                value_match_detail=compare_result.detail,
                blocking_errors=blocking_errors,
                field_category=compare_result.field_category,
                green_evidence=green_evidence,
                final_status=final_status.value,
                rule_score=round(rule_score, 4),
                rule_details=rule_details,
                counter_check_score=(
                    round(counter_check_score, 4)
                    if counter_check_score is not None
                    else None
                ),
                counter_check_notes=counter_check_notes,
                context_score=round(verify_score, 4),
                spatial_score=round(check2_confidence if check2_found else 0.0, 4),
                anchor_score=round(verify_score, 4),
                master_score=round(rule_score, 4),
                soft_score=round(soft_score, 4),
                verify_score=round(verify_score, 4),
                soft_breakdown={
                    "transform": round(_clamp(cell.confidence), 4),
                    "mapping": round(candidate_conf, 4),
                    "rules": round(rule_score, 4),
                },
                verify_breakdown={
                    "check2": round(
                        extraction.confidence if extraction.found else 0.0, 4
                    ),
                    "check3_match": (
                        1.0 if compare_result.result == MatchResult.MATCH else 0.0
                    ),
                },
                hard_vetoes=effective_hard_vetoes,
                decision_contract_version="v3_zero_false_positive",
                final_score=round(final_score, 4),
                classification=classification,
                reasoning=reasoning,
            )

            audit.cells = [*audit.cells, cell_audit]
            _increment_counts(audit, classification)

            # PERF-002: remember deferred counter-check candidates for the
            # page-batched verification after the loop. Promotion is only ever
            # decided by can_be_green on the FULL gate input — and only for
            # cells without warning/plausibility caps.
            if (
                deferred_page_number is not None
                and classification == TrafficLight.YELLOW
                and cell.target_field not in mv_warning_fields
                and (row.row_index, cell.source_column) not in plaus_cells
            ):
                deferred_counter_checks.append(
                    _DeferredCounterCheck(
                        audit_index=len(audit.cells) - 1,
                        page_number=deferred_page_number,
                        gate_input=gate_input,
                        target_field=cell.target_field,
                        source_column=cell.source_column,
                        primary_value=(
                            check2_extracted_value or transformed_val
                        ),
                        bbox_hint=(
                            str(source_location.bbox)
                            if source_location and source_location.bbox
                            else "unknown"
                        ),
                    )
                )

    # PERF-002/ARCH-002: page-batched counter-check for all deferred candidates.
    if deferred_counter_checks and counter_check_service is not None:
        await _run_batched_counter_checks(
            audit=audit,
            candidates=deferred_counter_checks,
            counter_check_service=counter_check_service,
            job_id=job_id or "",
            pdf_path=pdf_path or Path(transform_result.source_file),
        )

    # RB-1: stamp the deterministic band id onto every scored cell so the export
    # guard can assert the output by band identity (not position value).
    row_id_by_index = {
        row.row_index: row.source_row_id for row in transform_result.rows
    }
    for c in audit.cells:
        if not c.source_row_id:
            c.source_row_id = row_id_by_index.get(c.row_index, "")

    # B3: coverage guard — the LAST net against silent loss. Any master-set entry
    # (from the reconciler) not represented by a scored cell is added here as a
    # RED/MISSING cell so it surfaces as RED rather than vanishing.
    pos_field, pos_col = _position_field(schema)
    next_index = max((c.row_index for c in audit.cells), default=-1) + 1

    if transform_result.expected_row_keys:
        # RB-1 ROW-BAND mode (deterministic text-layer path): guard on band ids.
        scored_keys = {c.source_row_id for c in audit.cells if c.source_row_id}
        for missing_key in sorted(
            set(transform_result.expected_row_keys) - scored_keys
        ):
            if not missing_key:
                continue
            audit.cells = [
                *audit.cells,
                _make_missing_red_cell(
                    "", pos_field, pos_col, next_index, source_row_id=missing_key
                ),
            ]
            audit.red_count += 1
            audit.total_scored += 1
            next_index += 1
        audit.expected_row_keys = sorted(set(transform_result.expected_row_keys))
        audit.expected_position_count = len(audit.expected_row_keys)
    else:
        # POSITION mode (Vision/scan path) — unchanged.
        scored_positions = {
            _normalize_position_value(c.transformed_value)
            for c in audit.cells
            if c.target_field in _POSITION_FIELDS and c.transformed_value
        }
        for missing_pos in sorted(
            set(transform_result.expected_position_ids) - scored_positions
        ):
            if not missing_pos:
                continue
            audit.cells = [
                *audit.cells,
                _make_missing_red_cell(missing_pos, pos_field, pos_col, next_index),
            ]
            audit.red_count += 1
            audit.total_scored += 1
            next_index += 1
        # Position-set basis: guard threshold is the distinct master-set size.
        # Fallback basis (no anchor): keep the row-count threshold (ZDL-2).
        if transform_result.expected_position_ids:
            audit.expected_position_count = len(
                set(transform_result.expected_position_ids)
            )

    # ZDL-1: honest completeness verdict, surfaced to the dashboard.
    audit.completeness_guaranteed, audit.completeness_reason = _completeness_verdict(
        transform_result
    )

    # ARCH-003: tell the reviewer WHY a non-PDF source shows no GREEN at all.
    if not transform_result.source_is_pdf:
        audit.green_policy_note = (
            "Excel-/CSV-Quelle: GREEN ist für Nicht-PDF-Quellen deaktiviert "
            "(kein unabhängiger PDF-Beweis möglich). Werte wurden deterministisch "
            "übernommen — bitte im Review bestätigen."
        )

    logger.info(
        "Scored %s: %d cells | GREEN %d | YELLOW %d | RED %d | NEUTRAL %d | MANUAL %d",
        transform_result.source_file,
        audit.total_cells,
        audit.green_count,
        audit.yellow_count,
        audit.red_count,
        audit.neutral_count,
        audit.manual_confirmed_count,
    )
    return audit


def _score_empty_cell(
    *,
    audit: BomAuditTrail,
    row: TransformedRow,
    cell: CellTransformation,
    is_required: bool,
    source_location,
    candidate_conf: float,
    candidate_reason: str,
    has_blocking_validator_error: bool,
    empty_non_required_as_neutral: bool,
    empty_non_required_as_yellow: bool,
) -> None:
    raw_val = (cell.raw_value or "").strip()
    intentional_empty = (not is_required) and (raw_val == "")

    if intentional_empty:
        neutral_audit = CellAudit(
            row_index=row.row_index,
            target_field=cell.target_field,
            target_column=cell.target_column,
            source_column=cell.source_column,
            raw_value=cell.raw_value,
            transformed_value=cell.transformed_value,
            transform_method=cell.method,
            source_location=source_location,
            transform_confidence=1.0,
            mapping_confidence=round(candidate_conf, 4),
            candidate_confidence=round(candidate_conf, 4),
            candidate_reasoning=candidate_reason,
            value_match_result=MatchResult.NOT_APPLICABLE.value,
            value_match_detail="optional empty value",
            field_category="C",
            final_status=FinalStatus.NEUTRAL.value,
            rule_score=1.0,
            counter_check_score=None,
            counter_check_notes="counter_check_not_applicable_empty",
            context_score=1.0,
            spatial_score=1.0,
            anchor_score=1.0,
            master_score=1.0,
            soft_score=1.0,
            verify_score=1.0,
            is_neutral_empty=True,
            final_score=1.0,
            classification=TrafficLight.NEUTRAL,
            decision_contract_version="v3_zero_false_positive",
            reasoning="Optional field intentionally empty",
        )
        audit.cells = [*audit.cells, neutral_audit]
        audit.neutral_count += 1
        return

    hard_vetoes = [
        "MISSING_REQUIRED_VALUE" if is_required else "MAPPING_FAILURE_WITH_SOURCE_VALUE"
    ]
    blocking_errors = (
        ["MAPPING_VALIDATOR_ERROR"] if has_blocking_validator_error else []
    )

    if (
        (not is_required)
        and empty_non_required_as_neutral
        and not blocking_errors
        and raw_val == ""
    ):
        neutral_audit = CellAudit(
            row_index=row.row_index,
            target_field=cell.target_field,
            target_column=cell.target_column,
            source_column=cell.source_column,
            raw_value=cell.raw_value,
            transformed_value=cell.transformed_value,
            transform_method=cell.method,
            source_location=source_location,
            transform_confidence=0.0,
            mapping_confidence=round(candidate_conf, 4),
            candidate_confidence=round(candidate_conf, 4),
            candidate_reasoning=candidate_reason,
            value_match_result=MatchResult.NOT_APPLICABLE.value,
            value_match_detail="optional empty mapping review skipped",
            field_category="C",
            final_status=FinalStatus.NEUTRAL.value,
            rule_score=1.0,
            counter_check_score=None,
            counter_check_notes="counter_check_not_applicable_empty",
            context_score=1.0,
            spatial_score=1.0,
            anchor_score=1.0,
            master_score=1.0,
            soft_score=1.0,
            verify_score=1.0,
            is_neutral_empty=True,
            final_score=1.0,
            classification=TrafficLight.NEUTRAL,
            decision_contract_version="v3_zero_false_positive",
            reasoning="Optional field mapping failed, downgraded to NEUTRAL",
        )
        audit.cells = [*audit.cells, neutral_audit]
        audit.neutral_count += 1
        return

    if (not is_required) and empty_non_required_as_yellow and not blocking_errors:
        yellow_audit = CellAudit(
            row_index=row.row_index,
            target_field=cell.target_field,
            target_column=cell.target_column,
            source_column=cell.source_column,
            raw_value=cell.raw_value,
            transformed_value=cell.transformed_value,
            transform_method=cell.method,
            source_location=source_location,
            transform_confidence=0.0,
            mapping_confidence=round(candidate_conf, 4),
            candidate_confidence=round(candidate_conf, 4),
            candidate_reasoning=candidate_reason,
            value_match_result=MatchResult.UNCERTAIN.value,
            value_match_detail="empty value needs review",
            blocking_errors=blocking_errors,
            field_category="C",
            hard_vetoes=hard_vetoes,
            final_status=FinalStatus.YELLOW.value,
            rule_score=0.0,
            counter_check_score=None,
            counter_check_notes="counter_check_not_applicable_non_candidate",
            context_score=0.0,
            spatial_score=0.0,
            anchor_score=0.0,
            master_score=0.0,
            soft_score=0.0,
            verify_score=0.0,
            final_score=0.45,
            classification=TrafficLight.YELLOW,
            decision_contract_version="v3_zero_false_positive",
            reasoning="Optional field mapping failed, downgraded to YELLOW for review",
        )
        audit.cells = [*audit.cells, yellow_audit]
        audit.yellow_count += 1
        audit.total_scored += 1
        return

    red_audit = CellAudit(
        row_index=row.row_index,
        target_field=cell.target_field,
        target_column=cell.target_column,
        source_column=cell.source_column,
        raw_value=cell.raw_value,
        transformed_value=cell.transformed_value,
        transform_method=cell.method,
        source_location=source_location,
        transform_confidence=0.0,
        mapping_confidence=round(candidate_conf, 4),
        candidate_confidence=round(candidate_conf, 4),
        candidate_reasoning=candidate_reason,
        value_match_result=MatchResult.UNCERTAIN.value,
        value_match_detail="no transformed value",
        blocking_errors=blocking_errors,
        field_category="A" if is_required else "C",
        hard_vetoes=hard_vetoes,
        final_status=FinalStatus.RED.value,
        rule_score=0.0,
        counter_check_score=None,
        counter_check_notes="counter_check_not_applicable_non_candidate",
        context_score=0.0,
        spatial_score=0.0,
        anchor_score=0.0,
        master_score=0.0,
        soft_score=0.0,
        verify_score=0.0,
        final_score=0.0,
        classification=TrafficLight.RED,
        decision_contract_version="v3_zero_false_positive",
        reasoning="Missing required value or mapping failure",
    )
    audit.cells = [*audit.cells, red_audit]
    audit.red_count += 1
    audit.total_scored += 1


# Position-carrying target fields, in priority order (mirrors the reconciler).
# C2: keywords that identify a quantity/count field for plausibility checks.
_QUANTITY_FIELD_KEYWORDS = (
    "count",
    "qty",
    "quantity",
    "menge",
    "anzahl",
    "stück",
    "stck",
)
_DESCRIPTION_FIELD_KEYWORDS = ("description", "benennung", "bezeichnung", "désignation")


def _is_quantity_field(field_name: str) -> bool:
    name = (field_name or "").lower()
    return any(keyword in name for keyword in _QUANTITY_FIELD_KEYWORDS)


def _is_description_field(field_name: str) -> bool:
    name = (field_name or "").lower()
    return any(keyword in name for keyword in _DESCRIPTION_FIELD_KEYWORDS)


def _is_value_plausible(field_name: str, value: str | None) -> bool:
    """Cell-level plausibility for the Vision verified-scan GREEN path (C2).

    Blocks GREEN for obviously wrong OCR reads (e.g. qty 70000, empty/garbage
    designation). Returns True for any field type we do not sanity-check, so
    normal cells are never false-negatived.
    """
    text = (value or "").strip()

    if _is_quantity_field(field_name):
        try:
            quantity = int(float(text.replace(",", ".")))
        except (ValueError, TypeError):
            # Non-numeric quantity is handled by other checks — don't block here.
            return True
        return 0 < quantity < 10000

    if _is_description_field(field_name):
        alnum = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", text)
        return len(alnum) >= 2

    return True


def _completeness_verdict(transform_result: TransformationResult) -> tuple[bool, str]:
    """ZDL-1: decide whether output completeness rests on an INDEPENDENT anchor.

    Completeness is only guaranteed when the position set is verified against the
    digital PDF text layer. Scanned/Vision-only PDFs derive their position set
    from the extraction itself (no independent ground truth), so a whole row the
    model never read cannot be detected as missing — the verdict must say so.
    """
    if not transform_result.source_is_pdf:
        return False, "Quelle ist kein PDF — keine Positions-Reconciliation möglich."
    if transform_result.vision_fallback_reason:
        return (
            False,
            "Vision-Fallback auf Legacy-Parser — Vollständigkeit nicht garantiert.",
        )
    if transform_result.guard_basis not in {"position_set", "row_band_set"}:
        return (
            False,
            "Keine Positions-/Band-IDs als Anker "
            f"(guard_basis={transform_result.guard_basis}) — Vollständigkeit "
            "nicht garantiert.",
        )
    if not transform_result.has_text_layer:
        return (
            False,
            "Kein PDF-Text-Layer — Positions-Set stammt aus der Vision-Extraktion "
            "selbst (ZDL-1). Nicht gelesene Zeilen sind nicht erkennbar.",
        )
    if transform_result.guard_basis == "row_band_set":
        return (
            True,
            "Zeilen-Band-Set deterministisch aus dem PDF-Text-Layer rekonstruiert "
            "(unabhängiger Anker vor jedem LLM-Call, RB-1).",
        )
    return (
        True,
        "Positions-Set gegen den PDF-Text-Layer abgesichert (unabhängiger Anker).",
    )


_BATCH_COUNTER_CHECK_MAX_FIELDS = 12


async def _run_batched_counter_checks(
    *,
    audit: BomAuditTrail,
    candidates: list[_DeferredCounterCheck],
    counter_check_service: VisionCounterCheckService,
    job_id: str,
    pdf_path: Path,
) -> None:
    """Verify deferred GREEN candidates page-batched and promote via the gate.

    One Vision call per page (chunked at _BATCH_COUNTER_CHECK_MAX_FIELDS) instead
    of one per cell. A cell is promoted YELLOW→GREEN ONLY when can_be_green()
    passes on the full gate input with the counter-check result — every other
    safeguard (vetoes, plausibility, thresholds) stays in force. Any error keeps
    the cell YELLOW (conservative).
    """
    by_page: dict[int, list[_DeferredCounterCheck]] = {}
    for candidate in candidates:
        by_page.setdefault(candidate.page_number, []).append(candidate)

    for page_number in sorted(by_page):
        page_candidates = by_page[page_number]
        for start in range(0, len(page_candidates), _BATCH_COUNTER_CHECK_MAX_FIELDS):
            chunk = page_candidates[start : start + _BATCH_COUNTER_CHECK_MAX_FIELDS]
            requests = [
                BatchFieldRequest(
                    request_id=str(c.audit_index),
                    target_field=c.target_field,
                    source_column=c.source_column,
                    primary_value=c.primary_value,
                    bbox_hint=c.bbox_hint,
                )
                for c in chunk
            ]
            try:
                results = await counter_check_service.verify_fields(
                    job_id, pdf_path, page_number, requests
                )
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.warning(
                    "Batch counter-check failed for page %d: %s", page_number, exc
                )
                for c in chunk:
                    cell_audit = audit.cells[c.audit_index]
                    cell_audit.counter_check_score = 0.0
                    cell_audit.counter_check_notes = f"counter_check_error:{exc}"
                continue

            for c in chunk:
                cc_result = results.get(str(c.audit_index))
                cell_audit = audit.cells[c.audit_index]
                if cc_result is None:
                    cell_audit.counter_check_score = 0.0
                    cell_audit.counter_check_notes = "counter_check_batch_no_answer"
                    continue

                cell_audit.counter_check_score = round(cc_result.score, 4)
                cell_audit.counter_check_notes = cc_result.notes

                final_gate_input = replace(
                    c.gate_input,
                    counter_check_required=True,
                    counter_check_passed=cc_result.passed,
                )
                is_green, green_evidence = can_be_green(final_gate_input)
                if not is_green:
                    continue

                # Promote: the full gate (incl. counter-check) has passed.
                cell_audit.classification = TrafficLight.GREEN
                cell_audit.final_status = FinalStatus.GREEN.value
                cell_audit.green_evidence = green_evidence
                cell_audit.final_score = 1.0
                cell_audit.verify_score = 1.0
                cell_audit.context_score = 1.0
                cell_audit.reasoning = (
                    cell_audit.reasoning.replace("Decision=YELLOW", "Decision=GREEN", 1)
                    + " | PROMOTED_BY_BATCH_COUNTER_CHECK | GreenEvidence="
                    + ",".join(green_evidence)
                )
                audit.green_count += 1
                audit.yellow_count -= 1


def _position_field(schema: TargetSchema) -> tuple[str, str]:
    """Return (field_name, column) of the position field, defaulting to (…, 'A')."""
    for name in _POSITION_FIELDS:
        field_def = schema.field_by_name.get(name)
        if field_def is not None:
            return name, field_def.column
    return _POSITION_FIELDS[0], "A"


def _make_missing_red_cell(
    position: str,
    target_field: str,
    target_column: str,
    row_index: int,
    source_row_id: str = "",
) -> CellAudit:
    """Build a RED/MISSING audit cell for a position/band that was never extracted.

    Single source of truth for both the reconciler-synthesised rows (B2) and the
    coverage guard (B3). Hard-vetoed with RECONCILER_MISSING_POSITION so the
    GREEN gate can never promote it; the position is preserved in
    transformed_value for visibility, raw_value stays None. RB-1: ``source_row_id``
    carries the band identity so the export set-guard sees this row.
    """
    return CellAudit(
        row_index=row_index,
        source_row_id=source_row_id,
        target_field=target_field,
        target_column=target_column,
        source_column="",
        raw_value=None,
        transformed_value=position,
        transform_method="synthetic_pdf_only_missing",
        transform_confidence=0.0,
        value_match_result=MatchResult.MISMATCH.value,
        value_match_detail="position present in PDF but missing from extracted rows",
        field_category="A",
        hard_vetoes=["RECONCILER_MISSING_POSITION"],
        final_status=FinalStatus.RED.value,
        rule_score=0.0,
        counter_check_score=None,
        counter_check_notes="counter_check_not_applicable_pdf_only_position",
        context_score=0.0,
        spatial_score=0.0,
        anchor_score=0.0,
        master_score=0.0,
        soft_score=0.0,
        verify_score=0.0,
        final_score=0.0,
        classification=TrafficLight.RED,
        decision_contract_version="v3_zero_false_positive",
        reasoning=_ERP_MISSING_POSITION_REASON,
    )


def _resolve_source_location(
    transform_result: TransformationResult,
    row: TransformedRow,
    cell: CellTransformation,
):
    if not cell.source_column:
        return None
    return transform_result.source_locations.get(row.row_index, {}).get(
        cell.source_column
    )


def _resolve_counter_check_page_number(
    source_location: SourceLocation | None,
    extracted_source_location: str,
) -> int | None:
    if (
        source_location
        and isinstance(source_location.page, int)
        and source_location.page > 0
    ):
        return source_location.page

    match = re.search(r"(?:^|;)page=(\d+)", extracted_source_location or "")
    if match:
        return int(match.group(1))
    return None


def _compute_rule_score(
    *,
    cell: CellTransformation,
    field_def,
    cv_severity: str | None,
) -> tuple[float, list[str]]:
    score = 0.0
    details: list[str] = []
    value = (cell.transformed_value or "").strip()

    if value:
        score += 0.30
        details.append("+0.30 non-empty")

    if _check_type(value, field_def):
        score += 0.30
        details.append("+0.30 type")
    else:
        details.append("+0.00 type")

    method_quality = _method_quality_score(cell.method)
    score += 0.25 * method_quality
    details.append(f"+{0.25 * method_quality:.2f} method")

    if cv_severity == "error":
        details.append("+0.00 cv:error")
    elif cv_severity == "warning":
        score += 0.05
        details.append("+0.05 cv:warning")
    else:
        score += 0.15
        details.append("+0.15 cv:ok")

    return _clamp(score), details


def _check_type(value: str, field_def: Any) -> bool:
    if field_def is None:
        return True

    expected_type = getattr(field_def, "type", "string")
    if expected_type == "integer":
        return bool(re.fullmatch(r"[-+]?\d+", value))

    if expected_type == "decimal":
        normalized = value.replace(",", ".")
        return bool(re.fullmatch(r"[-+]?\d*\.?\d+", normalized))

    return True


def _method_quality_score(method: str) -> float:
    quality_map = {
        "master_data:exact_alias": 1.0,
        "master_data:exact": 1.0,
        "master_data:fuzzy_alias": 0.95,
        "master_data:fuzzy_material": 0.92,
        "master_data:werkstoff_nr_extract": 0.95,
        "master_data:werkstoff_nr_base": 0.90,
        # M3: structurally-valid DIN Werkstoffnummer recognised by format (not in
        # catalog). Below the catalog-confirmed methods (0.95-1.0), above generic
        # passthrough — a correctly-read material id earns a green-eligible rule
        # score. Green stays gated by text-path + value_match in the green gate.
        "master_data:werkstoff_nr_format": 0.90,
        # DATA-004: reconstructed (dot-swallowed) number — suggestion quality only.
        "master_data:werkstoff_nr_stripped": 0.75,
        "integer_coerce": 0.95,
        "decimal_coerce": 0.90,
        "boolean_normalize": 0.95,
        "regex_parse": 0.85,
        "dimension_split": 0.85,
        "text_cleanup": 0.80,
        "master_data:substring": 0.60,
        "extracted_from_material": 0.55,
        "passthrough": 0.82,
        "empty": 0.0,
    }
    return quality_map.get(method, 0.50)


def _compute_final_score(
    classification: TrafficLight,
    soft_score: float,
    verify_score: float,
) -> float:
    if classification == TrafficLight.GREEN:
        return 1.0
    if classification == TrafficLight.YELLOW:
        return _clamp(0.45 + 0.30 * soft_score + 0.25 * verify_score)
    if classification == TrafficLight.NEUTRAL:
        return 1.0
    if classification == TrafficLight.MANUAL_CONFIRMED:
        return 1.0
    return _clamp(0.10 + 0.20 * soft_score)


def _compute_verify_score(
    *,
    gate_input: GreenGateInput,
    compare_result,
    is_green: bool,
    effective_hard_vetoes: list[str],
) -> float:
    if is_green:
        return 1.0

    if gate_input.blocking_errors or effective_hard_vetoes:
        return 0.0

    if gate_input.extraction_method == ExtractionMethod.PYMUPDF_TEXT:
        score = 0.0

        if gate_input.pdf_extracted_found:
            score += 0.15

        if (
            gate_input.pdf_extraction_confidence
            >= gate_input.green_extraction_min_confidence
        ):
            score += 0.20
        elif gate_input.pdf_extraction_confidence >= 0.70:
            score += 0.10

        if text_path_transform_verified(gate_input):
            score += 0.20

        if gate_input.rule_score >= gate_input.verify_green_threshold:
            score += 0.15
        elif gate_input.rule_score >= gate_input.soft_green_floor:
            score += 0.05

        if gate_input.candidate_confidence >= 0.90:
            score += 0.15
        elif gate_input.candidate_confidence >= 0.75:
            score += 0.10

        if compare_result.result == MatchResult.MATCH:
            score += 0.15
        elif compare_result.result == MatchResult.UNCERTAIN:
            score += 0.10

        return _clamp(score)

    return 0.6 if compare_result.result == MatchResult.MATCH else 0.0


def _build_reasoning(
    *,
    classification: TrafficLight,
    compare_result,
    extraction_confidence: float,
    extraction_reason: str,
    candidate_conf: float,
    rule_score: float,
    hard_vetoes: list[str],
    blocking_errors: list[str],
    green_evidence: list[str],
) -> str:
    parts = [
        f"Decision={classification.value.upper()}",
        f"CHECK3={compare_result.result.value}",
        f"Detail={compare_result.detail}",
        f"Check2Conf={extraction_confidence:.2f}",
        f"Check2Reason={extraction_reason}",
        f"CandidateConf={candidate_conf:.2f}",
        f"RuleScore={rule_score:.2f}",
    ]

    if blocking_errors:
        parts.append(f"Blocking={','.join(blocking_errors)}")

    if hard_vetoes:
        parts.append(f"HardVeto={','.join(hard_vetoes)}")

    if green_evidence:
        parts.append(f"GreenEvidence={','.join(green_evidence)}")

    return " | ".join(parts)


def _extract_pdf_positions_from_pages(
    document_text_pages: list[str],
) -> list[_PDFPositionEvidence]:
    positions: list[_PDFPositionEvidence] = []

    for page_index, page_text in enumerate(document_text_pages):
        for line_index, line in enumerate(_page_text_lines(page_text)):
            matches = list(_PDF_POSITION_RE.finditer(line))
            if not matches:
                continue

            for match_index, match in enumerate(matches):
                normalized_position = _normalize_position_value(match.group("pos"))
                if not normalized_position:
                    continue

                next_start = (
                    matches[match_index + 1].start()
                    if match_index + 1 < len(matches)
                    else len(line)
                )
                remainder = line[match.end() : next_start].strip(" |\t")
                customer_part_number = _extract_pdf_customer_part_number(
                    remainder,
                    normalized_position,
                )
                design_count = _extract_pdf_design_count(remainder)
                description = _extract_pdf_description(
                    remainder,
                    customer_part_number,
                    design_count,
                )
                positions.append(
                    _PDFPositionEvidence(
                        position=normalized_position,
                        line_text=line,
                        page_index=page_index,
                        line_index=line_index,
                        customer_part_number=customer_part_number,
                        design_count=design_count,
                        description=description,
                    )
                )

    return positions


def _extract_pdf_positions_from_document_text(
    document_text_layer: str,
) -> list[_PDFPositionEvidence]:
    return _extract_pdf_positions_from_pages(
        _split_document_text_layer_into_pages(document_text_layer)
    )


def _split_document_text_layer_into_pages(document_text_layer: str) -> list[str]:
    if not (document_text_layer or "").strip():
        return []

    pages = [
        page.strip()
        for page in re.split(r"\n\s*--- PAGE BREAK ---\s*\n", document_text_layer)
        if page.strip()
    ]
    return pages or [document_text_layer.strip()]


# B1 safety: the layout-aware text layer is annotated with "ROW NNN:" prefixes
# and "[x=N-M]" coordinate tags (see pdf_parser._render_layout_aware_page_text).
# Those scaffolding numbers (row indices, pixel x-ranges) must be stripped before
# position scanning, otherwise the broadened position patterns capture them as
# phantom positions (e.g. "[x=482-806]" → "482-806").
_LAYOUT_ROW_PREFIX_RE = re.compile(r"^ROW\s+\d+\s*:\s*", re.IGNORECASE)
_LAYOUT_XTAG_RE = re.compile(r"\[\s*x\s*=\s*-?\d+\s*-\s*-?\d+\s*\]")


def _strip_layout_scaffolding(line: str) -> str:
    """Remove ROW/x-coordinate annotations and cell separators from a text line."""
    line = _LAYOUT_ROW_PREFIX_RE.sub("", line)
    line = _LAYOUT_XTAG_RE.sub(" ", line)
    line = line.replace("||", " ")
    return " ".join(line.split())


def _page_text_lines(document_text_layer: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (document_text_layer or "").splitlines():
        cleaned = _strip_layout_scaffolding(" ".join(raw_line.strip().split()))
        if not cleaned or cleaned == "--- PAGE BREAK ---":
            continue
        lines.append(cleaned)
    return lines


def _extract_pdf_customer_part_number(
    text: str,
    position: str,
) -> str | None:
    normalized_position = _normalize_position_value(position)
    for match in _PDF_CUSTOMER_PART_RE.finditer(text or ""):
        token = match.group(0).strip()
        if _normalize_position_value(token) == normalized_position:
            continue
        if token.casefold() in {"stk", "stck", "pcs", "pc", "ea"}:
            continue
        return token
    return None


def _extract_pdf_design_count(text: str) -> str | None:
    candidates: list[int] = []
    for match in _PDF_QUANTITY_RE.finditer(text or ""):
        value = match.group(0).strip()
        parsed = _parse_pdf_quantity_int(value)
        if parsed is not None:
            candidates.append(parsed)

    if not candidates:
        return None
    return str(candidates[-1])


def _extract_pdf_description(
    text: str,
    customer_part_number: str | None,
    design_count: str | None,
) -> str | None:
    description = text or ""
    if customer_part_number:
        description = re.sub(
            re.escape(customer_part_number),
            " ",
            description,
            count=1,
            flags=re.IGNORECASE,
        )
    if design_count:
        description = re.sub(
            rf"(?<![A-Z0-9]){re.escape(design_count)}(?:\s*(?:stk|stck|pcs|pc|ea|x))?(?![A-Z0-9])",
            " ",
            description,
            count=1,
            flags=re.IGNORECASE,
        )
    description = re.sub(r"\s+", " ", description).strip(" -|;")
    return description or None


def _parse_pdf_quantity_int(value: str | None) -> int | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned:
        return None

    cleaned = re.sub(r"([.,])0+\b", "", cleaned)
    if re.search(r"[.,]\d", cleaned):
        return None

    digits_only = re.sub(r"\D+", "", cleaned)
    if not digits_only:
        return None

    try:
        return int(digits_only)
    except ValueError:
        return None


def _increment_counts(audit: BomAuditTrail, classification: TrafficLight) -> None:
    if classification == TrafficLight.GREEN:
        audit.green_count += 1
    elif classification == TrafficLight.YELLOW:
        audit.yellow_count += 1
    elif classification == TrafficLight.NEUTRAL:
        audit.neutral_count += 1
    elif classification == TrafficLight.MANUAL_CONFIRMED:
        audit.manual_confirmed_count += 1
    else:
        audit.red_count += 1

    if classification != TrafficLight.NEUTRAL:
        audit.total_scored += 1


def _build_mapping_confidence_map(mapping: MappingResult) -> dict[str, float]:
    by_target: dict[str, float] = {}
    for item in mapping.mappings:
        if item.target_field:
            by_target[item.target_field] = float(item.confidence)
    return by_target


def _build_mapping_reasoning_map(mapping: MappingResult) -> dict[str, str]:
    by_target: dict[str, str] = {}
    for item in mapping.mappings:
        if item.target_field:
            by_target[item.target_field] = item.reasoning or item.candidate_reasoning
    return by_target


def _index_cv_issues(cv_result: CrossValidationResult) -> dict[tuple[int, str], str]:
    index: dict[tuple[int, str], str] = {}
    severity_rank = {"error": 3, "warning": 2, "info": 1}
    for issue in cv_result.issues:
        key = (issue.row_index, issue.field)
        existing = index.get(key)
        if existing is None or severity_rank.get(issue.severity, 0) > severity_rank.get(
            existing, 0
        ):
            index[key] = issue.severity
    return index


def _index_cv_contradictions(cv_result: CrossValidationResult) -> set[tuple[int, str]]:
    contradictions: set[tuple[int, str]] = set()
    for issue in cv_result.issues:
        if issue.severity != "error":
            continue
        if "ENG_CONTRADICTION" in issue.message:
            contradictions.add((issue.row_index, issue.field))
    return contradictions


def _index_row_validation_flags(
    transform_result: TransformationResult,
) -> tuple[
    set[tuple[int, str]],
    set[tuple[int, str]],
    set[tuple[int, str]],
    set[tuple[int, str]],
]:
    coord_mismatch_cells: set[tuple[int, str]] = set()
    coord_column_conflict_cells: set[tuple[int, str]] = set()
    dual_mismatch_cells: set[tuple[int, str]] = set()
    plaus_cells: set[tuple[int, str]] = set()

    for row_idx, flags in transform_result.row_validation_flags.items():
        for flag in flags:
            if flag.startswith("COORDMISS:"):
                col_name = _extract_flag_column(flag[10:])
                if col_name:
                    coord_mismatch_cells.add((row_idx, col_name))
                continue

            if flag.startswith("COORDCOL:"):
                col_name = _extract_flag_column(flag[9:])
                if col_name:
                    coord_column_conflict_cells.add((row_idx, col_name))
                continue

            if flag.startswith("DUAL:"):
                col_name = _extract_flag_column(flag[5:])
                if col_name:
                    dual_mismatch_cells.add((row_idx, col_name))
                continue

            if flag.startswith("PLAUS:"):
                col_name = _extract_flag_column(flag[6:])
                if col_name:
                    plaus_cells.add((row_idx, col_name))

    return coord_mismatch_cells, coord_column_conflict_cells, dual_mismatch_cells, plaus_cells


def _extract_flag_column(flag_payload: str) -> str:
    payload = flag_payload.strip()
    if not payload:
        return ""
    return payload.split(":", 1)[0].strip()


def _index_mapping_validation(
    mv: ValidationResult | None,
) -> tuple[set[str], set[str]]:
    if mv is None:
        return set(), set()

    errors = {
        issue.target_field
        for issue in mv.issues
        if issue.severity == "error" and issue.target_field
    }
    warnings = {
        issue.target_field
        for issue in mv.issues
        if issue.severity == "warning" and issue.target_field
    }
    return errors, warnings


def _serialize_mapping_validation(
    mv: ValidationResult | None,
) -> list[MappingValidationFinding]:
    if mv is None:
        return []

    return [
        MappingValidationFinding(
            severity=issue.severity,
            message=issue.message,
            source_column=issue.source_column,
            target_field=issue.target_field,
        )
        for issue in mv.issues
    ]


def _clamp(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN != NaN is always True
        return 0.0
    return max(0.0, min(1.0, v))
