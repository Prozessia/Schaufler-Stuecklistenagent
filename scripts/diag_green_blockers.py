"""Green-blocker audit (no Azure): WHERE is green lost across all fields, and WHY.

Heuristically maps each file's source columns to the target schema (high mapping
confidence, so mapping is NOT the variable under test), scores deterministically,
and aggregates per target field: green / yellow / red, plus a histogram of the
dominant blocker for every non-green, non-empty cell. Pure measurement.

Usage:
    python scripts/diag_green_blockers.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.coordinate_table import reconstruct_table  # noqa: E402
from src.ingestion.pdf_common import ExtractionError  # noqa: E402
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult  # noqa: E402
from src.mapping.schema_registry import load_schema  # noqa: E402
from src.reconciliation.position_reconciler import reconcile_positions  # noqa: E402
from src.scoring.ensemble_scorer import score_bom_async  # noqa: E402
from src.scoring.threshold_manager import load_scoring_config  # noqa: E402
from src.transform.pipeline import transform_bom  # noqa: E402

INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "input" / "PDF_POC"

# target field -> source-header keywords (first matching header wins)
_FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Detail Number": ("pos", "detail", "pozice", "foglio"),
    "Description": ("benennung", "bezeichnung", "description", "popis", "denominazione", "teil"),
    "Design Count": ("stk", "stück", "stck", "anzahl", "menge", "qty", "quantity", "množství", "stato"),
    "Material": ("werkst", "material", "vergüt", "matér", "materiale", "norma"),
    "Dimensions X/D": ("fertigma", "maße", "masse", "rozměr", "dimension", "finished", "abmaß", "rohmass"),
    "Hardness": ("härte", "hrc", "hardness", "rc"),
}


class _StubLLM:
    pass


def _heuristic_mapping(headers: list[str], schema) -> MappingResult:
    tf = schema.field_by_name
    used: set[str] = set()
    mappings: list[ColumnMapping] = []
    for field, keywords in _FIELD_KEYWORDS.items():
        if field not in tf:
            continue
        for h in headers:
            if h in used:
                continue
            if any(k in h.lower() for k in keywords):
                mappings.append(
                    ColumnMapping(
                        source_column=h, target_field=field, target_column=tf[field].column,
                        confidence=0.96, reasoning="heuristic", candidate_confidence=0.96,
                        candidate_reasoning="heuristic",
                    )
                )
                used.add(h)
                break
    return MappingResult(source_file="x", customer="x", mappings=mappings)


def _blocker(cell) -> str:
    if cell.hard_vetoes:
        return f"veto:{cell.hard_vetoes[0]}"
    vmr = (cell.value_match_result or "").lower()
    if vmr == "mismatch":
        return "value_mismatch(canonicalization/wrong)"
    if cell.rule_score < 0.90:
        return f"low_rule_score(method={cell.transform_method})"
    if vmr == "uncertain":
        return "value_uncertain"
    return f"other(method={cell.transform_method},match={vmr})"


async def _score(path: Path, schema, config):
    bom = await reconstruct_table(path, _StubLLM())
    mapping = _heuristic_mapping(bom.headers, schema)
    if not mapping.mappings:
        return None
    tr = transform_bom(bom, mapping, schema)
    reconcile_positions(tr, bom.raw_pdf_positions, schema, pdf_row_bands=bom.pdf_row_bands)
    return await score_bom_async(tr, mapping, schema=schema, config=config)


async def main() -> None:
    schema = load_schema()
    config = load_scoring_config()
    per_field: dict[str, Counter] = defaultdict(Counter)
    blockers: Counter = Counter()
    blockers_by_field: dict[str, Counter] = defaultdict(Counter)

    for pdf in sorted(INPUT_DIR.rglob("*.pdf")):
        try:
            audit = await _score(pdf, schema, config)
        except ExtractionError:
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {pdf.name}: {exc}")
            continue
        if audit is None:
            continue
        for c in audit.cells:
            cls = c.classification.value
            per_field[c.target_field][cls] += 1
            if cls in ("yellow", "red") and (c.transformed_value or "").strip():
                b = _blocker(c)
                blockers[b] += 1
                blockers_by_field[c.target_field][b] += 1

    print("=== GREEN / YELLOW / RED per target field (mapped fields only) ===")
    for field in _FIELD_KEYWORDS:
        cnt = per_field.get(field)
        if not cnt:
            continue
        g, y, r = cnt.get("green", 0), cnt.get("yellow", 0), cnt.get("red", 0)
        tot = g + y + r + cnt.get("neutral", 0)
        scored = g + y + r
        pct = f"{100*g/scored:.0f}%" if scored else "-"
        print(f"  {field:16s} green={g:5d} yellow={y:5d} red={r:5d} | green%={pct}")
        for b, n in blockers_by_field[field].most_common(3):
            print(f"        non-green: {n:5d}  {b}")

    print("\n=== TOP green-blockers overall (non-green, non-empty cells) ===")
    for b, n in blockers.most_common(12):
        print(f"  {n:6d}  {b}")


if __name__ == "__main__":
    asyncio.run(main())
