"""Regression test: parse all PDF files via Vision pipeline and report results."""

import asyncio
import os
import sys
import glob

os.environ["PYMUPDF_MESSAGE"] = "path:" + os.devnull

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.ingestion.structure_normalizer import parse_file
from src.llm.azure_openai import AzureOpenAILLM


async def main():
    llm = AzureOpenAILLM()

    pdf_dir = "data/input/PDF_POC"
    pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "**/*.pdf"), recursive=True))

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regression_results.txt")
    lines = []

    ok = 0
    fail = 0
    total_tokens_in = 0
    total_tokens_out = 0

    for f in pdf_files:
        rel = os.path.relpath(f, pdf_dir)
        try:
            bom = await parse_file(f, llm=llm)
            method = bom.source.extraction_method.value if bom.source.extraction_method else "none"
            h = len(bom.headers)
            r = len(bom.rows)
            flags = bom.metadata.get("validation_flags_total", 0)
            status = "OK" if r > 0 else "EMPTY"
            line = f"{status:5s} h={h:>2} r={r:>5} flags={flags:>3} {method:<15s} {rel}"
            lines.append(line)
            print(line)
            if r > 0:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            line = f"ERR   {rel}: {e}"
            lines.append(line)
            print(line)
            fail += 1

    lines.append(f"\nSUMMARY: OK={ok} Failed={fail} Total={len(pdf_files)}")
    text = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as fout:
        fout.write(text)
    print(f"\nSUMMARY: OK={ok} Failed={fail} Total={len(pdf_files)}")


if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    main()
