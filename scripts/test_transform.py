"""Test script: Run value transformation on ALL BOM files and show results.

This script parses each BOM, runs the column mapping (mocked from saved results
or live LLM), then applies Phase 4 value transformation + cross-validation.

Usage:
    python scripts/test_transform.py                    # Full pipeline (needs LLM)
    python scripts/test_transform.py --skip-llm         # Use saved mapping results
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_transform")

DATA_DIR = PROJECT_ROOT / "data" / "input" / "PDF_POC"
SAVED_MAPPINGS_FILE = PROJECT_ROOT / "data" / "test_outputs" / "mapping_results.json"


def find_bom_files() -> list[Path]:
    """Find all customer BOM files."""
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
    """Load mapping results from previous test_mapping.py runs.

    The saved file stores evaluation results (with 'details' containing
    actual_source/actual_target/confidence per mapping).
    """
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
    """Reconstruct a MappingResult from saved evaluation data.

    The saved format has 'details' with: actual_source, actual_target, confidence.
    We reconstruct ColumnMapping objects from these.
    """
    if schema is None:
        schema = load_schema()

    # Build column lookup for target → column letter
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
                mappings.append(ColumnMapping(
                    source_column=src,
                    target_field=tgt,
                    target_column=field_to_col.get(tgt, ""),
                    confidence=conf,
                    reasoning="from saved evaluation",
                ))

    return MappingResult(
        source_file=bom.source.filename,
        customer=bom.source.customer,
        mappings=mappings,
    )


def print_transform_summary(customer: str, result, cv_result) -> None:
    """Print a nice summary of transformation results."""
    stats = result.stats
    print(f"\n{'=' * 70}")
    print(f"  {customer} - {result.source_file}")
    print(f"{'=' * 70}")
    print(f"  Rows: {result.total_rows}")
    print(f"  Total cells: {stats['total_cells']}")
    print(f"  Transformed:  {stats['transformed']}")
    print(f"  Passthrough:  {stats['passthrough']}")
    print(f"  Empty:        {stats['empty']}")
    print(f"  ---")
    print(f"  Dimension splits:   {stats['dimension_split']}")
    print(f"  Hardness parsed:    {stats['hardness_parsed']}")
    print(f"  Master-data matched: {stats['master_data_matched']}")
    if stats.get("inch_converted", 0) > 0:
        print(f"  Inch->mm converted: {stats['inch_converted']}")
    print(f"  Avg confidence:     {result.avg_confidence:.2f}")
    print(f"  ---")
    print(f"  {cv_result.summary()}")

    # Show sample transformations (first 3 rows, key fields only)
    key_fields = [
        "Detail Number", "Design Count", "Description",
        "Dimensions X/D", "Dimensions Y/L", "Dimensions Z",
        "Material", "Hardness", "Nitriding",
    ]
    print(f"\n  Sample transformations (first 3 rows):")
    for row in result.rows[:3]:
        print(f"\n  Row {row.row_index}:")
        for cell in row.cells:
            if cell.target_field in key_fields and (cell.raw_value or cell.transformed_value):
                raw_disp = (cell.raw_value or "")[:40]
                trans_disp = (cell.transformed_value or "")[:40]
                if raw_disp != trans_disp:
                    print(f"    {cell.target_field:20s}: '{raw_disp}' → '{trans_disp}' [{cell.method}, conf={cell.confidence:.2f}]")
                else:
                    print(f"    {cell.target_field:20s}: '{trans_disp}' [{cell.method}, conf={cell.confidence:.2f}]")

    # Show cross-validation issues
    if cv_result.issues:
        print(f"\n  Cross-validation issues:")
        for issue in cv_result.issues[:10]:
            print(f"    [{issue.severity.upper():7s}] Row {issue.row_index}, {issue.field}: {issue.message}")
        if len(cv_result.issues) > 10:
            print(f"    ... and {len(cv_result.issues) - 10} more")


async def main():
    skip_llm = "--skip-llm" in sys.argv

    schema = load_schema()
    logger.info("Loaded target schema with %d fields", len(schema.fields))

    files = find_bom_files()
    logger.info("Found %d BOM files", len(files))

    # Load saved mappings if available
    saved_mappings = load_saved_mappings()
    if saved_mappings:
        logger.info("Loaded saved mappings for %d customers", len(saved_mappings))

    # LLM for live mapping
    llm = None
    if not skip_llm:
        try:
            from src.llm.azure_openai import AzureOpenAILLM
            llm = AzureOpenAILLM()
        except Exception as e:
            logger.warning("LLM init failed: %s — will use saved mappings only", e)

    seen_customers: set[str] = set()
    all_stats = {
        "customers_processed": 0,
        "total_rows": 0,
        "total_cells": 0,
        "total_transformed": 0,
        "total_passthrough": 0,
        "total_empty": 0,
        "total_dim_splits": 0,
        "total_hardness": 0,
        "total_master_data": 0,
        "cv_errors": 0,
        "cv_warnings": 0,
    }

    for filepath in files:
        customer_key = get_customer_key(filepath)
        if customer_key in seen_customers:
            continue
        seen_customers.add(customer_key)

        # Parse the BOM
        try:
            bom = parse_file(filepath)
        except Exception as e:
            logger.error("Parse failed for %s: %s", filepath.name, e)
            continue

        if not bom.headers or not bom.rows:
            logger.warning("Empty BOM: %s", filepath.name)
            continue

        # Get mapping (saved or live)
        mapping = None
        if customer_key in saved_mappings:
            mapping = reconstruct_mapping_result(saved_mappings[customer_key], bom)
            logger.info("Using saved mapping for %s", customer_key)
        elif llm:
            try:
                mapping = await map_columns(bom, llm, schema)
                logger.info("Live mapping for %s: %d mappings", customer_key, mapping.mapped_count)
            except Exception as e:
                logger.error("Mapping failed for %s: %s", customer_key, e)
                continue
        else:
            logger.warning("No mapping available for %s (no saved mapping, no LLM)", customer_key)
            continue

        if mapping.mapped_count == 0:
            logger.warning("No mappings for %s — skipping transform", customer_key)
            continue

        # Transform
        result = transform_bom(bom, mapping, schema)

        # Cross-validate
        cv_result = cross_validate(result)

        # Print summary
        print_transform_summary(customer_key, result, cv_result)

        # Accumulate stats
        all_stats["customers_processed"] += 1
        all_stats["total_rows"] += result.total_rows
        all_stats["total_cells"] += result.stats["total_cells"]
        all_stats["total_transformed"] += result.stats["transformed"]
        all_stats["total_passthrough"] += result.stats["passthrough"]
        all_stats["total_empty"] += result.stats["empty"]
        all_stats["total_dim_splits"] += result.stats["dimension_split"]
        all_stats["total_hardness"] += result.stats["hardness_parsed"]
        all_stats["total_master_data"] += result.stats["master_data_matched"]
        all_stats["cv_errors"] += cv_result.error_count
        all_stats["cv_warnings"] += cv_result.warning_count

    # Final summary
    print(f"\n{'=' * 70}")
    print(f"  OVERALL SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Customers processed: {all_stats['customers_processed']}")
    print(f"  Total rows:          {all_stats['total_rows']}")
    print(f"  Total cells:         {all_stats['total_cells']}")
    print(f"  Transformed:         {all_stats['total_transformed']} ({_pct(all_stats['total_transformed'], all_stats['total_cells'])})")
    print(f"  Passthrough:         {all_stats['total_passthrough']} ({_pct(all_stats['total_passthrough'], all_stats['total_cells'])})")
    print(f"  Empty:               {all_stats['total_empty']} ({_pct(all_stats['total_empty'], all_stats['total_cells'])})")
    print(f"  ---")
    print(f"  Dimension splits:    {all_stats['total_dim_splits']}")
    print(f"  Hardness parsed:     {all_stats['total_hardness']}")
    print(f"  Master-data matched: {all_stats['total_master_data']}")
    print(f"  ---")
    print(f"  CV errors:           {all_stats['cv_errors']}")
    print(f"  CV warnings:         {all_stats['cv_warnings']}")


def _pct(part: int, total: int) -> str:
    return f"{part / total * 100:.1f}%" if total > 0 else "0%"


if __name__ == "__main__":
    asyncio.run(main())
