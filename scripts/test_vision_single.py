"""Quick test: run Vision pipeline on a single PDF."""
import asyncio
import os
import sys
import json

os.environ["PYMUPDF_MESSAGE"] = "path:" + os.devnull
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.ingestion.structure_normalizer import parse_file
from src.llm.azure_openai import AzureOpenAILLM


async def test():
    llm = AzureOpenAILLM()

    # Use TCG file — was problematic with column bleeding before
    pdf = "data/input/PDF_POC/TCG/8489/Kunde/stckliste_ohne_ver0001.pdf"
    print(f"Testing: {pdf}")
    bom = await parse_file(pdf, llm=llm)

    print(f"Method: {bom.source.extraction_method.value}")
    print(f"Headers ({len(bom.headers)}): {bom.headers}")
    print(f"Rows: {len(bom.rows)}")
    flags = bom.metadata.get("validation_flags_total", 0)
    print(f"Validation flags: {flags}")

    # Show first 5 rows
    for i, row in enumerate(bom.rows[:5]):
        clean = {k: v for k, v in row.items() if v is not None and not k.startswith("_")}
        print(f"  Row {i}: {json.dumps(clean, ensure_ascii=False)}")

    # Show flagged rows from metadata
    row_flags = bom.metadata.get("row_validation_flags", {})
    if row_flags:
        print(f"\nFlagged rows: {len(row_flags)}")
        for row_idx in sorted(row_flags.keys())[:5]:
            clean = {k: v for k, v in bom.rows[row_idx].items() if v is not None}
            print(f"  Row {row_idx}: {json.dumps(clean, ensure_ascii=False)}")
            print(f"  Flags: {row_flags[row_idx]}")
    else:
        print("\nNo flagged rows — all values passed plausibility checks")


if __name__ == "__main__":
    asyncio.run(test())
