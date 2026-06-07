"""Quick test: run Vision pipeline on a few targeted PDFs."""
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


TEST_FILES = [
    # GF — was EMPTY with old parser
    "data/input/PDF_POC/GF/STL_08.05.13.pdf",
    # Linamar — small file, quick test
    "data/input/PDF_POC/Linamar FR/Beispiel 1/8185-13_GE2 DIE2_2024_TDDA_Workbook_TEMPLATE.pdf",
    # ZF — worked well before, verify no regression
    "data/input/PDF_POC/ZF/Projekt 7497/Kunde/f156900400_stl.pdf",
]


async def main():
    llm = AzureOpenAILLM()

    for pdf in TEST_FILES:
        if not os.path.exists(pdf):
            print(f"SKIP  {pdf} (not found)")
            continue

        print(f"\n{'='*60}")
        print(f"Testing: {pdf}")
        try:
            bom = await parse_file(pdf, llm=llm)
            method = bom.source.extraction_method.value if bom.source.extraction_method else "none"
            h = len(bom.headers)
            r = len(bom.rows)
            flags = bom.metadata.get("validation_flags_total", 0)
            row_flags = bom.metadata.get("row_validation_flags", {})
            status = "OK" if r > 0 else "EMPTY"
            print(f"  Status: {status} | Method: {method} | Headers: {h} | Rows: {r} | Flags: {flags}")
            print(f"  Headers: {bom.headers}")

            # Show first 3 rows
            for i, row in enumerate(bom.rows[:3]):
                clean = {k: v for k, v in row.items() if v is not None}
                print(f"  Row {i}: {json.dumps(clean, ensure_ascii=False)}")

            if row_flags:
                print(f"  Flagged rows: {len(row_flags)}")
                for ridx in sorted(row_flags.keys())[:2]:
                    print(f"    Row {ridx}: {row_flags[ridx]}")

        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n{'='*60}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
