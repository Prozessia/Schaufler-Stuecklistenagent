"""Test script: Full pipeline Parse -> Map -> Transform -> Score for ALL BOMs.

Shows Green/Yellow/Red distribution per customer and overall.
Validates green cells by printing them for manual inspection.

Usage:
    python scripts/test_scoring.py                    # Full (needs LLM)
    python scripts/test_scoring.py --skip-llm         # Use saved mappings
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import Counter
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_scoring")

DATA_DIR = PROJECT_ROOT / "data" / "input" / "PDF_POC"
SAVED_MAPPINGS_FILE = PROJECT_ROOT / "data" / "test_outputs" / "mapping_results.json"


# ---------------------------------------------------------------------------
# Helpers (same as test_transform.py)
# ---------------------------------------------------------------------------


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


def reconstruct_mapping_result(
    saved: dict, bom: ParsedBOM, schema=None
) -> MappingResult:
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


# ---------------------------------------------------------------------------
# Scoring summary printer
# ---------------------------------------------------------------------------


def print_scoring_summary(customer: str, audit: BomAuditTrail) -> None:
    """Print per-customer scoring distribution."""
    print(f"\n{'=' * 70}")
    print(f"  {customer} - {audit.source_file}")
    print(f"{'=' * 70}")
    print(f"  Total scored cells:  {audit.total_scored}")
    print(
        f"  NEUTRAL: {audit.neutral_count:5d}  ({audit.neutral_pct:5.1f}% of all cells)"
    )
    print(f"  GREEN:   {audit.green_count:5d}  ({audit.green_pct:5.1f}%)")
    print(f"  YELLOW:  {audit.yellow_count:5d}  ({audit.yellow_pct:5.1f}%)")
    print(f"  RED:     {audit.red_count:5d}  ({audit.red_pct:5.1f}%)")

    # Show a few green cells for manual validation
    greens = audit.get_cells_by_classification(TrafficLight.GREEN)
    if greens:
        # Group by target_field
        by_field: dict[str, list] = {}
        for c in greens:
            by_field.setdefault(c.target_field, []).append(c)

        print("\n  GREEN cell samples (for manual validation):")
        shown = 0
        for field_name, cells in sorted(by_field.items()):
            if shown >= 15:
                break
            # Show first 2 per field
            for c in cells[:2]:
                raw_d = (c.raw_value or "")[:35]
                trans_d = (c.transformed_value or "")[:35]
                if raw_d != trans_d and raw_d:
                    print(
                        f"    [{c.final_score:.2f}] {field_name:20s}: '{raw_d}' -> '{trans_d}' ({c.transform_method})"
                    )
                else:
                    print(
                        f"    [{c.final_score:.2f}] {field_name:20s}: '{trans_d}' ({c.transform_method})"
                    )
                shown += 1

    # Show a few yellow cells
    yellows = audit.get_cells_by_classification(TrafficLight.YELLOW)
    if yellows:
        print("\n  YELLOW cell samples (need review):")
        for c in yellows[:5]:
            raw_d = (c.raw_value or "")[:35]
            trans_d = (c.transformed_value or "")[:35]
            print(
                f"    [{c.final_score:.2f}] {c.target_field:20s}: '{raw_d}' -> '{trans_d}' ({c.transform_method})"
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    schema = load_schema()
    config = load_scoring_config()
    logger.info(
        "Scoring config: green >= %.2f, yellow >= %.2f, conservative=%s, verify_contract=%s, verify_green >= %.2f, soft_floor >= %.2f",
        config.green_threshold,
        config.yellow_threshold,
        config.conservative_mode,
        config.verify_contract_enabled,
        config.verify_green_threshold,
        config.soft_green_floor,
    )

    files = find_bom_files()
    logger.info("Found %d BOM files", len(files))

    saved_mappings = load_saved_mappings()
    if saved_mappings:
        logger.info("Loaded saved mappings for %d customers", len(saved_mappings))

    llm = None
    try:
        from src.llm.azure_openai import AzureOpenAILLM

        llm = AzureOpenAILLM()
    except (ImportError, EnvironmentError, TypeError, ValueError) as e:
        logger.warning("LLM init failed: %s", e)

    from src.scoring.vision_verifier import VisionCounterCheckService

    counter_check_service: VisionCounterCheckService | None = None
    if llm is not None:
        counter_check_service = VisionCounterCheckService(llm)
        print("[TEST CONFIG] counter_check_service: ACTIVE (production mode)")
        print("[TEST CONFIG] CHECK5 Vision Counter-Check: ENABLED")
    else:
        print("[TEST CONFIG] counter_check_service: DISABLED (LLM not available)")
        print("[TEST CONFIG] CHECK5 Vision Counter-Check: DISABLED")

    seen_customers: set[str] = set()
    totals = {
        "customers": 0,
        "parser_total": 0,
        "parser_success": 0,
        "cells": 0,
        "neutral": 0,
        "green": 0,
        "yellow": 0,
        "red": 0,
        "promoted_green": 0,
        "promoted_green_risky": 0,
        "unsafe_green_proxy": 0,
        "required_field_false_green": 0,
    }
    yellow_by_field: Counter[str] = Counter()
    hard_veto_counter: Counter[str] = Counter()

    # Collect all green cells for validation report
    all_green_transformed: list[dict] = []

    for filepath in files:
        customer_key = get_customer_key(filepath)
        if customer_key in seen_customers:
            continue
        seen_customers.add(customer_key)
        totals["parser_total"] += 1

        # Parse
        try:
            if filepath.suffix.lower() == ".pdf" and llm is None:
                logger.warning(
                    "Skipping PDF without LLM parser client: %s", filepath.name
                )
                continue
            bom = await parse_file(filepath, llm=llm)
            totals["parser_success"] += 1
        except (RuntimeError, ValueError, OSError, TypeError) as e:
            logger.error("Parse failed for %s: %s", filepath.name, e)
            continue

        if not bom.headers or not bom.rows:
            logger.warning("Empty BOM: %s", filepath.name)
            continue

        # Get mapping
        mapping = None
        if customer_key in saved_mappings:
            mapping = reconstruct_mapping_result(
                saved_mappings[customer_key], bom, schema
            )
        elif llm:
            try:
                mapping = await map_columns(bom, llm, schema)
            except (RuntimeError, ValueError, OSError, TypeError) as e:
                logger.error("Mapping failed for %s: %s", customer_key, e)
                continue
        else:
            logger.warning("No mapping for %s", customer_key)
            continue

        if mapping.mapped_count == 0:
            logger.warning("No mappings for %s", customer_key)
            continue

        # Transform
        transform_result = transform_bom(bom, mapping, schema)

        # Cross-validate
        cv_result = cross_validate(transform_result)

        # Score
        _job_id = filepath.stem
        _pdf_path = filepath if filepath.suffix.lower() == ".pdf" else None
        audit = await score_bom_async(
            transform_result,
            mapping,
            cv_result,
            schema,
            config,
            counter_check_service=counter_check_service,
            job_id=_job_id,
            pdf_path=_pdf_path,
        )
        if counter_check_service is not None:
            counter_check_service.release_job(_job_id)

        # Print
        print_scoring_summary(customer_key, audit)

        # Accumulate
        totals["customers"] += 1
        totals["cells"] += audit.total_scored
        totals["neutral"] += audit.neutral_count
        totals["green"] += audit.green_count
        totals["yellow"] += audit.yellow_count
        totals["red"] += audit.red_count

        for c in audit.cells:
            if c.classification == TrafficLight.YELLOW:
                yellow_by_field[c.target_field] += 1
            if c.hard_vetoes:
                for veto in c.hard_vetoes:
                    hard_veto_counter[veto] += 1

            if c.classification == TrafficLight.GREEN:
                details = " | ".join(c.rule_details)
                mismatch_like = (
                    "MISMATCH" in details
                    or "CONFLICT" in details
                    or "CONTRADICTION" in details
                    or "FLAGGED" in details
                    or "CV error" in details
                    or bool(c.hard_vetoes)
                )

                if c.promotion_reason:
                    totals["promoted_green"] += 1
                    if mismatch_like:
                        totals["promoted_green_risky"] += 1

                if mismatch_like:
                    totals["unsafe_green_proxy"] += 1

                if c.target_field in schema.field_by_name:
                    if schema.field_by_name[c.target_field].required:
                        raw_val = (c.raw_value or "").strip()
                        out_val = (c.transformed_value or "").strip()
                        if raw_val == "" and out_val == "":
                            totals["required_field_false_green"] += 1

        # Collect green cells that had actual transformations (not just passthrough text)
        for c in audit.get_cells_by_classification(TrafficLight.GREEN):
            if c.raw_value and c.raw_value != c.transformed_value:
                all_green_transformed.append(
                    {
                        "customer": customer_key,
                        "row": c.row_index,
                        "field": c.target_field,
                        "raw": c.raw_value,
                        "transformed": c.transformed_value,
                        "method": c.transform_method,
                        "score": c.final_score,
                    }
                )

    # Final summary
    total_cells = totals["cells"]
    print(f"\n{'=' * 70}")
    print("  OVERALL SCORING SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Customers: {totals['customers']}")
    parser_success_rate = (
        totals["parser_success"] / totals["parser_total"] * 100
        if totals["parser_total"]
        else 0.0
    )
    print(
        f"  Parser success: {totals['parser_success']}/{totals['parser_total']}"
        f" ({parser_success_rate:.1f}%)"
    )
    print(f"  Total cells scored: {total_cells}")
    print(f"  Total neutral cells: {totals['neutral']}")
    print(
        f"  GREEN:   {totals['green']:6d}  ({totals['green']/total_cells*100:.1f}%)"
        if total_cells
        else ""
    )
    print(
        f"  YELLOW:  {totals['yellow']:6d}  ({totals['yellow']/total_cells*100:.1f}%)"
        if total_cells
        else ""
    )
    print(
        f"  RED:     {totals['red']:6d}  ({totals['red']/total_cells*100:.1f}%)"
        if total_cells
        else ""
    )

    print(f"  Promoted GREEN cells: {totals['promoted_green']}")
    print(f"  Promoted GREEN risky: {totals['promoted_green_risky']}")
    print(f"  Unsafe GREEN proxy:   {totals['unsafe_green_proxy']}")
    print(f"  Required false-green: {totals['required_field_false_green']}")

    if yellow_by_field:
        print("\n  Top YELLOW fields:")
        for field, count in yellow_by_field.most_common(12):
            print(f"    {field:25s} {count:6d}")

    if hard_veto_counter:
        print("\n  Hard-veto distribution:")
        for veto, count in hard_veto_counter.most_common():
            print(f"    {veto:25s} {count:6d}")

    # Green validation report
    if all_green_transformed:
        print(f"\n{'=' * 70}")
        print("  GREEN CELLS WITH TRANSFORMATIONS (for manual validation)")
        print(
            f"  Total: {len(all_green_transformed)} cells had value changes AND were scored GREEN"
        )
        print(f"{'=' * 70}")
        # Group by field type for easier review
        by_field: dict[str, list] = {}
        for item in all_green_transformed:
            by_field.setdefault(item["field"], []).append(item)

        for field_name in sorted(by_field.keys()):
            items = by_field[field_name]
            print(f"\n  --- {field_name} ({len(items)} green-transformed) ---")
            # Show sample (up to 8 unique transformations)
            seen_transforms: set[str] = set()
            for item in items:
                sig = f"{item['raw'][:30]}|{item['transformed'][:30]}"
                if sig in seen_transforms:
                    continue
                seen_transforms.add(sig)
                if len(seen_transforms) > 8:
                    print(f"    ... and {len(items) - 8} more")
                    break
                print(
                    f"    [{item['score']:.2f}] '{item['raw'][:40]}' -> '{item['transformed'][:40]}'"
                    f"  ({item['method']}, {item['customer']})"
                )

    if counter_check_service is not None:
        counter_check_service.close()


if __name__ == "__main__":
    asyncio.run(main())
