"""Diagnose GREEN gate: trace why cells are YELLOW/RED and find opportunity space.

Usage:
    python scripts/diagnose_green_gate.py --skip-llm
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.core.models import ParsedBOM
from src.ingestion.structure_normalizer import parse_file
from src.mapping.llm_column_mapper import MappingResult, ColumnMapping, map_columns
from src.mapping.schema_registry import load_schema
from src.transform.pipeline import transform_bom
from src.transform.cross_validator import cross_validate
from src.scoring.ensemble_scorer import score_bom_async
from src.scoring.threshold_manager import load_scoring_config, TrafficLight
from src.scoring.audit_trail import BomAuditTrail

DATA_DIR = PROJECT_ROOT / "data" / "input" / "PDF_POC"
SAVED_MAPPINGS_FILE = PROJECT_ROOT / "data" / "test_outputs" / "mapping_results.json"


def find_bom_files() -> list[Path]:
    files = []
    for f in sorted(DATA_DIR.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".pdf", ".xlsx"):
            continue
        if "CadCam_Stuecklistenvorlage" in f.name:
            continue
        files.append(f)
    return files


def get_customer_key(filepath: Path) -> str:
    parts = filepath.parts
    try:
        poc_idx = parts.index("PDF_POC")
        return parts[poc_idx + 1]
    except (ValueError, IndexError):
        return "unknown"


def load_saved_mappings() -> dict[str, dict]:
    if not SAVED_MAPPINGS_FILE.exists():
        return {}
    data = json.loads(SAVED_MAPPINGS_FILE.read_text(encoding="utf-8"))
    by_customer = {}
    for entry in data:
        if "error" not in entry and entry.get("has_ground_truth"):
            by_customer[entry.get("customer", "")] = entry
    return by_customer


def reconstruct_mapping_result(saved: dict, bom: ParsedBOM, schema=None):
    if schema is None:
        schema = load_schema()
    field_to_col = {f.name: f.column for f in schema.fields}
    mappings = []
    seen_sources = set()
    for detail in saved.get("details", []):
        src = detail.get("actual_source")
        tgt = detail.get("actual_target")
        conf = detail.get("confidence", 0.0)
        if src and tgt and detail.get("status") == "CORRECT":
            if src not in seen_sources:
                seen_sources.add(src)
                mappings.append(
                    ColumnMapping(
                        source_column=src,
                        target_field=tgt,
                        target_column=field_to_col.get(tgt, ""),
                        confidence=conf,
                        reasoning="from saved evaluation",
                    )
                )
    return MappingResult(
        source_file=bom.source.filename,
        customer=bom.source.customer,
        mappings=mappings,
    )


async def main():
    schema = load_schema()
    config = load_scoring_config()

    print(f"verify_green_threshold = {config.verify_green_threshold}")
    print(f"green_threshold = {config.green_threshold}")
    print(f"enable_counter_check = {config.enable_counter_check}")

    files = find_bom_files()
    saved_mappings = load_saved_mappings()

    llm = None
    try:
        from src.llm.azure_openai import AzureOpenAILLM

        llm = AzureOpenAILLM()
    except Exception:
        pass

    # Aggregate counters
    total = Counter()
    block_reason_counter = Counter()
    yellow_by_method = Counter()
    yellow_by_field = Counter()
    yellow_match_status = Counter()
    green_by_method = Counter()
    green_by_field = Counter()

    # Track "correct but stuck" cells (YELLOW with CHECK3=MATCH)
    stuck_true_positives = Counter()  # by blocking reason
    stuck_by_field = Counter()
    stuck_by_method = Counter()

    seen_customers: set[str] = set()

    for filepath in files:
        customer_key = get_customer_key(filepath)
        if customer_key in seen_customers:
            continue
        seen_customers.add(customer_key)

        try:
            bom = await parse_file(filepath, llm=llm)
        except Exception as e:
            print(f"SKIP {customer_key}: parse error: {e}")
            continue

        if not bom.headers or not bom.rows:
            continue

        mapping = None
        if customer_key in saved_mappings:
            mapping = reconstruct_mapping_result(
                saved_mappings[customer_key], bom, schema
            )
        elif llm:
            try:
                mapping = await map_columns(bom, llm, schema)
            except Exception:
                continue
        else:
            continue

        if mapping.mapped_count == 0:
            continue

        transform_result = transform_bom(bom, mapping, schema)
        cv_result = cross_validate(transform_result)

        audit = await score_bom_async(
            transform_result,
            mapping,
            cv_result,
            schema,
            config,
        )

        total["customers"] += 1
        total["scored"] += audit.total_scored
        total["green"] += audit.green_count
        total["yellow"] += audit.yellow_count
        total["red"] += audit.red_count
        total["neutral"] += audit.neutral_count

        for c in audit.cells:
            if c.classification == TrafficLight.GREEN:
                green_by_method[c.transform_method] += 1
                green_by_field[c.target_field] += 1

            elif c.classification == TrafficLight.YELLOW:
                yellow_by_method[c.transform_method] += 1
                yellow_by_field[c.target_field] += 1
                yellow_match_status[c.value_match_result] += 1

                # Parse reasoning to find what blocked GREEN
                reasoning = c.reasoning or ""
                for tag in [
                    "CHECK3_NOT_MATCH",
                    "CHECK4_RULE_SCORE_BELOW_VERIFY_THRESHOLD",
                    "NO_PDF_EVIDENCE",
                    "NO_PDF_TEXT_LAYER",
                    "CHECK2_EXTRACTION_MISSING",
                    "CHECK2_EXTRACTION_LOW_CONFIDENCE",
                    "VISION_FALLBACK_TO_LEGACY_PARSER",
                    "CHECK5_COUNTER_CHECK_FAILED",
                    "CATEGORY_A_REQUIRES_EXACT_MATCH",
                    "MAPPING_VALIDATOR_WARNING_CAP",
                ]:
                    if tag in reasoning:
                        block_reason_counter[tag] += 1

                # "Stuck true positive" = YELLOW but CHECK3 = MATCH
                if c.value_match_result == "match":
                    stuck_true_positives["total"] += 1
                    stuck_by_field[c.target_field] += 1
                    stuck_by_method[c.transform_method] += 1
                    # Find what specifically blocked it
                    if "CHECK4_RULE_SCORE_BELOW_VERIFY_THRESHOLD" in reasoning:
                        stuck_true_positives["blocked_by_rule_score"] += 1
                    if "CHECK5_COUNTER_CHECK_FAILED" in reasoning:
                        stuck_true_positives["blocked_by_counter_check"] += 1
                    if "MAPPING_VALIDATOR_WARNING_CAP" in reasoning:
                        stuck_true_positives["blocked_by_mv_warning"] += 1

            elif c.classification == TrafficLight.RED:
                if c.hard_vetoes:
                    for v in c.hard_vetoes:
                        block_reason_counter[f"RED_VETO:{v}"] += 1

        print(
            f"  {customer_key:25s} G={audit.green_count:5d} Y={audit.yellow_count:5d} R={audit.red_count:5d} N={audit.neutral_count:5d}"
        )

    print(f"\n{'='*70}")
    print(f"  DIAGNOSTIC SUMMARY (current code)")
    print(f"{'='*70}")
    print(f"  Customers: {total['customers']}")
    print(f"  Scored:    {total['scored']}")
    scored = total["scored"] or 1
    print(f"  GREEN:     {total['green']:6d} ({total['green']/scored*100:.1f}%)")
    print(f"  YELLOW:    {total['yellow']:6d} ({total['yellow']/scored*100:.1f}%)")
    print(f"  RED:       {total['red']:6d} ({total['red']/scored*100:.1f}%)")
    print(f"  NEUTRAL:   {total['neutral']:6d}")

    print(f"\n  GREEN by method:")
    for method, count in green_by_method.most_common(20):
        print(f"    {method:40s} {count:6d}")

    print(f"\n  GREEN by field:")
    for field, count in green_by_field.most_common(20):
        print(f"    {field:40s} {count:6d}")

    print(f"\n  YELLOW by method:")
    for method, count in yellow_by_method.most_common(20):
        print(f"    {method:40s} {count:6d}")

    print(f"\n  YELLOW by field:")
    for field, count in yellow_by_field.most_common(20):
        print(f"    {field:40s} {count:6d}")

    print(f"\n  YELLOW match status:")
    for status, count in yellow_match_status.most_common():
        print(f"    {status:40s} {count:6d}")

    print(f"\n  Block reasons (from YELLOW reasoning):")
    for reason, count in block_reason_counter.most_common(20):
        print(f"    {reason:50s} {count:6d}")

    print(f"\n{'='*70}")
    print(f"  STUCK TRUE POSITIVES (YELLOW with CHECK3=MATCH)")
    print(f"{'='*70}")
    print(f"  Total: {stuck_true_positives.get('total', 0)}")
    print(
        f"  Blocked by rule_score < verify_threshold: {stuck_true_positives.get('blocked_by_rule_score', 0)}"
    )
    print(
        f"  Blocked by counter_check failure:         {stuck_true_positives.get('blocked_by_counter_check', 0)}"
    )
    print(
        f"  Blocked by mapping_validator warning:      {stuck_true_positives.get('blocked_by_mv_warning', 0)}"
    )

    print(f"\n  Stuck by method:")
    for method, count in stuck_by_method.most_common(20):
        print(f"    {method:40s} {count:6d}")

    print(f"\n  Stuck by field:")
    for field, count in stuck_by_field.most_common(20):
        print(f"    {field:40s} {count:6d}")

    # Compute theoretical max GREEN if all stuck true positives were unlocked
    theoretical_green = total["green"] + stuck_true_positives.get("total", 0)
    print(
        f"\n  Theoretical max GREEN (all stuck TPs unlocked): {theoretical_green} ({theoretical_green/scored*100:.1f}%)"
    )
    print(f"  Current GREEN rate: {total['green']/scored*100:.1f}%")
    print(
        f"  Potential uplift: +{stuck_true_positives.get('total', 0)} cells (+{stuck_true_positives.get('total', 0)/scored*100:.1f}%)"
    )


if __name__ == "__main__":
    asyncio.run(main())
