"""Test script: Run column mapping against ALL BOM files and evaluate accuracy.

Usage:
    python scripts/test_mapping.py

Requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY in environment or .env file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.core.models import ParsedBOM
from src.ingestion.structure_normalizer import parse_file
from src.llm.azure_openai import AzureOpenAILLM
from src.mapping.schema_registry import load_schema
from src.mapping.llm_column_mapper import map_columns, MappingResult
from src.mapping.mapping_validator import validate_mapping

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_mapping")

DATA_DIR = PROJECT_ROOT / "data" / "input" / "PDF_POC"


# ---------------------------------------------------------------------------
# Ground-truth mapping matrix (from Phase 1 analysis report)
# Maps: customer_key → { source_column_substring → target_field_name }
#
# This is the manual/expected mapping for the known test files.
# It only needs to cover the KEY fields — not every column.
# ---------------------------------------------------------------------------

GROUND_TRUTH: dict[str, dict[str, str]] = {
    "audi": {
        "Pos Nr.": "Detail Number",
        "Anzahl Normal": "Design Count",
        "Anzahl Ersatzteile": "Spare Count",
        "Teilebezeichnung": "Description",
        "Fertigmaße": "Dimensions X/D",
        "Material / DIN": "Material",
        "N/mm² HB HRc": "Hardness",
        "Nitrieren": "Nitriding",
        "Hinweise oder Bemerkungen": "Special Notes",
        "Lieferant": "Manufacturer",
    },
    "FCA": {
        "DET.": "Detail Number",
        "QTY": "Design Count",
        "DESCRIPTION": "Description",
        "MATERIAL": "Material",
        "FINISHED SIZE": "Dimensions X/D",
    },
    "Ford": {
        "DETAIL NUMBER": "Detail Number",
        "QUANTITY": "Design Count",
        "SPARES": "Spare Count",
        "DESCRIPTION": "Description",
        "Material": "Material",
        "Rc": "Hardness",
        "Surface Treatment": ["Nitriding", "Coating"],
        "SUPPLIER": "Manufacturer",
        "REMARKS": "Special Notes",
    },
    "GF": {
        "Pos.": "Detail Number",
        "St.": "Design Count",
        "Bezeichnung": "Description",
        "Werkst.": "Material",
        "TG": "Parts Group",
    },
    "Linamar FR": {
        "PART NUMBER": "Customer Part Number",
        "DIMENSION": "Dimensions X/D",
        "TOOL COMPOSITION": "Material",
    },
    "Ljunghaell_Tschechien": {
        "Pozice": "Detail Number",
        "Množství": "Design Count",
        "Náhradní": "Spare Count",
        "POPIS": "Description",
        "Material": "Material",
        "ČistýRozměr": "Dimensions X/D",
        "Dodavatel": "Manufacturer",
        "Č. DÍLU": "Customer Part Number",
    },
    "Magna": {
        "Pos Nr.": "Detail Number",
        "Anzahl Normal": "Design Count",
        "Anzahl Ersatzteile": "Spare Count",
        "Teilebezeichnung": "Description",
        "Fertigmaße": "Dimensions X/D",
        "Material / DIN": "Material",
        "N/mm² HB HRc": "Hardness",
        "Lieferant": "Manufacturer",
        "Hinweise oder Bemerkungen": "Special Notes",
    },
    "Mercedes": {
        "Pos.": "Detail Number",
        "Stck.": "Design Count",
        "Benennung": "Description",
        "Sachnummer": "Customer Part Number",
        "Material": "Material",
        "Fertigmaß": "Dimensions X/D",
    },
    "Scania": {
        "Pos.": "Detail Number",
        "Qua.": "Design Count",
        "TIPO DI MATERIALE": "Material",
        "DUREZZA": "Hardness",
        "X / Ø / M": "Dimensions X/D",
    },
    "TCG": {
        "POS": "Detail Number",
        "BENENNUNG": "Description",
        "STK": "Design Count",
        "WERKST": "Material",
        "FERTIGMASS": "Dimensions X/D",
        "BEMERKUNG": "Special Notes",
    },
    "ZF": {
        "Pos. Nr.": "Detail Number",
        "Anzahl": "Design Count",
        "Bezeichnung": "Description",
        "Fertigma": "Dimensions X/D",
        "rte HRC": "Hardness",
        "Hinweise": "Special Notes",
    },
}


def find_bom_files() -> list[Path]:
    """Find all customer BOM files (exclude Schaufler templates)."""
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
    """Extract customer folder name from path."""
    parts = filepath.parts
    try:
        poc_idx = parts.index("PDF_POC")
        return parts[poc_idx + 1]
    except (ValueError, IndexError):
        return "unknown"


import re
import unicodedata


def _normalize_for_match(s: str) -> str:
    """Normalize a string for fuzzy matching.

    Handles PDF-mangled characters: ² → 2, ä → a, special chars stripped.
    Removes all non-alphanumeric chars for robust substring matching.
    """
    # NFKD decomposition splits ² → 2, ä → a + combining diaeresis, etc.
    s = unicodedata.normalize("NFKD", s)
    # Remove combining characters (diacritics)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Lowercase, keep only alphanumeric
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s


def evaluate_mapping(
    result: MappingResult,
    customer_key: str,
) -> dict:
    """Compare mapping result against ground truth.

    Returns a dict with accuracy metrics.
    """
    gt = GROUND_TRUTH.get(customer_key, {})
    if not gt:
        return {
            "customer": customer_key,
            "file": result.source_file,
            "has_ground_truth": False,
            "total_gt_mappings": 0,
            "correct": 0,
            "incorrect": 0,
            "missing": 0,
            "accuracy": None,
            "details": [],
        }

    correct = 0
    incorrect = 0
    missing = 0
    details = []

    for gt_source_substr, gt_target_spec in gt.items():
        # gt_target_spec can be a string or a list of acceptable target names
        acceptable_targets = (
            gt_target_spec
            if isinstance(gt_target_spec, list)
            else [gt_target_spec]
        )
        gt_target = acceptable_targets[0]  # Primary target for display

        # Find the mapping that matches this ground-truth source column
        # Prefer exact matches over substring matches
        matched_mapping = None
        best_match_quality = 0  # 0=none, 1=substring, 2=exact
        gt_norm = _normalize_for_match(gt_source_substr)
        for m in result.mappings:
            src_norm = _normalize_for_match(m.source_column)
            if gt_norm == src_norm:
                matched_mapping = m
                best_match_quality = 2
                break  # Exact match, stop searching
            elif best_match_quality < 1 and (gt_norm in src_norm or src_norm in gt_norm):
                # Substring match — but only if the shorter string is >= 4 chars
                # to avoid false positives like "des" matching "description"
                shorter = min(len(gt_norm), len(src_norm))
                if shorter >= 4:
                    matched_mapping = m
                    best_match_quality = 1

        if matched_mapping is None:
            missing += 1
            details.append(
                {
                    "gt_source": gt_source_substr,
                    "gt_target": gt_target,
                    "actual_source": None,
                    "actual_target": None,
                    "status": "MISSING",
                }
            )
        elif matched_mapping.target_field in acceptable_targets:
            correct += 1
            details.append(
                {
                    "gt_source": gt_source_substr,
                    "gt_target": gt_target,
                    "actual_source": matched_mapping.source_column,
                    "actual_target": matched_mapping.target_field,
                    "confidence": matched_mapping.confidence,
                    "status": "CORRECT",
                }
            )
        else:
            incorrect += 1
            details.append(
                {
                    "gt_source": gt_source_substr,
                    "gt_target": gt_target,
                    "actual_source": matched_mapping.source_column,
                    "actual_target": matched_mapping.target_field,
                    "confidence": matched_mapping.confidence,
                    "status": "INCORRECT",
                }
            )

    total = correct + incorrect + missing
    accuracy = correct / total if total > 0 else 0.0

    return {
        "customer": customer_key,
        "file": result.source_file,
        "has_ground_truth": True,
        "total_gt_mappings": total,
        "correct": correct,
        "incorrect": incorrect,
        "missing": missing,
        "accuracy": accuracy,
        "details": details,
    }


async def main():
    # Initialize LLM
    try:
        llm = AzureOpenAILLM()
    except EnvironmentError as e:
        logger.error("Cannot initialize LLM: %s", e)
        logger.error("Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY in .env")
        sys.exit(1)

    schema = load_schema()
    logger.info("Loaded target schema with %d fields", len(schema.fields))

    # Find all BOM files
    files = find_bom_files()
    logger.info("Found %d BOM files to process", len(files))

    # Process each file
    all_results: list[dict] = []
    total_tokens_in = 0
    total_tokens_out = 0
    seen_customers: set[str] = set()

    for filepath in files:
        customer_key = get_customer_key(filepath)

        # Skip duplicate customer formats (same schema, different project)
        # Only process the FIRST file per customer to save tokens
        if customer_key in seen_customers:
            logger.info("Skipping %s (already tested %s)", filepath.name, customer_key)
            continue
        seen_customers.add(customer_key)

        logger.info("=" * 60)
        logger.info("Processing: %s / %s", customer_key, filepath.name)

        # Parse
        try:
            bom = parse_file(filepath)
        except Exception as e:
            logger.error("Parse failed for %s: %s", filepath.name, e)
            all_results.append(
                {
                    "customer": customer_key,
                    "file": filepath.name,
                    "error": f"Parse failed: {e}",
                }
            )
            continue

        if bom.total_rows == 0:
            logger.warning("No rows parsed for %s — skipping mapping", filepath.name)
            all_results.append(
                {
                    "customer": customer_key,
                    "file": filepath.name,
                    "error": "No rows parsed",
                }
            )
            continue

        logger.info("Parsed: %d headers, %d rows", bom.total_columns, bom.total_rows)
        logger.info("Headers: %s", bom.headers[:10])

        # Map columns
        try:
            mapping_result = await map_columns(bom, llm, schema)
        except Exception as e:
            logger.error("Mapping failed for %s: %s", filepath.name, e)
            all_results.append(
                {
                    "customer": customer_key,
                    "file": filepath.name,
                    "error": f"Mapping failed: {e}",
                }
            )
            continue

        # Track tokens
        if mapping_result.llm_response:
            total_tokens_in += mapping_result.llm_response.tokens_input
            total_tokens_out += mapping_result.llm_response.tokens_output

        # Validate
        validation = validate_mapping(mapping_result, bom, schema)
        for issue in validation.issues:
            logger.info("  [%s] %s", issue.severity.upper(), issue.message)

        # Evaluate against ground truth
        eval_result = evaluate_mapping(mapping_result, customer_key)
        all_results.append(eval_result)

        # Print mapping summary
        print(f"\n{'=' * 60}")
        print(f"CUSTOMER: {customer_key}")
        print(f"FILE: {filepath.name}")
        print(f"SOURCE COLUMNS: {bom.total_columns}")
        print(
            f"MAPPED: {mapping_result.mapped_count}/{mapping_result.total_source_columns}"
        )
        print(f"AVG CONFIDENCE: {mapping_result.avg_confidence:.2f}")
        print()
        for m in mapping_result.mappings:
            if m.target_field:
                print(
                    f"  {m.source_column:40s} → {m.target_field:30s} ({m.confidence:.2f}) {m.reasoning}"
                )
            else:
                print(f"  {m.source_column:40s} → (unmapped)  {m.reasoning}")

        if eval_result.get("has_ground_truth"):
            print(f"\nGROUND TRUTH EVALUATION:")
            print(
                f"  Correct: {eval_result['correct']}/{eval_result['total_gt_mappings']}"
            )
            print(f"  Incorrect: {eval_result['incorrect']}")
            print(f"  Missing: {eval_result['missing']}")
            print(f"  Accuracy: {eval_result['accuracy']:.1%}")
            for d in eval_result["details"]:
                status_icon = {"CORRECT": "✓", "INCORRECT": "✗", "MISSING": "?"}.get(
                    d["status"], " "
                )
                print(
                    f"    [{status_icon}] {d['gt_source']:30s} → expected: {d['gt_target']:25s} | got: {d.get('actual_target', 'N/A')}"
                )

    # ---------------------------------------------------------------------------
    # Final summary
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    gt_results = [r for r in all_results if r.get("has_ground_truth")]
    error_results = [r for r in all_results if "error" in r]

    total_correct = sum(r["correct"] for r in gt_results)
    total_gt = sum(r["total_gt_mappings"] for r in gt_results)
    total_incorrect = sum(r["incorrect"] for r in gt_results)
    total_missing = sum(r["missing"] for r in gt_results)
    overall_accuracy = total_correct / total_gt if total_gt > 0 else 0.0

    print(f"\nFiles processed: {len(all_results)}")
    print(f"Files with errors: {len(error_results)}")
    print(f"Files with ground truth: {len(gt_results)}")
    print(f"\nOVERALL MAPPING ACCURACY: {overall_accuracy:.1%}")
    print(f"  Correct: {total_correct}/{total_gt}")
    print(f"  Incorrect: {total_incorrect}/{total_gt}")
    print(f"  Missing: {total_missing}/{total_gt}")

    print(f"\nPer-customer accuracy:")
    for r in gt_results:
        acc = r["accuracy"]
        print(
            f"  {r['customer']:25s} {acc:.1%}  ({r['correct']}/{r['total_gt_mappings']})"
        )

    print(
        f"\nToken usage: {total_tokens_in:,} input + {total_tokens_out:,} output = {total_tokens_in + total_tokens_out:,} total"
    )

    # Save detailed results to JSON
    output_path = PROJECT_ROOT / "data" / "test_outputs" / "mapping_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nDetailed results saved to: {output_path}")

    return overall_accuracy


if __name__ == "__main__":
    accuracy = asyncio.run(main())
    sys.exit(0 if accuracy and accuracy >= 0.9 else 1)
