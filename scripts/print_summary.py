"""Extract structured summary from raw analysis for the report."""

import json
from pathlib import Path

BASE = Path(r"c:\Users\douioui\Documents\privat\Schaufler-Stücklistenagent")

with open(BASE / "data" / "analysis" / "raw_analysis.json", "r", encoding="utf-8") as f:
    data = json.load(f)


def print_excel_summary(r):
    print(f"  File: {r['relative_path']}")
    print(f"  Size: {r['size_kb']} KB")
    print(f"  Is Target Template: {r.get('is_target_template', False)}")
    if "error" in r:
        print(f"  ERROR: {r['error']}")
        return
    print(f"  Sheets: {r.get('sheets', [])}")
    for sname, sdata in r.get("sheets_data", {}).items():
        print(f"\n  --- Sheet: '{sname}' ---")
        print(f"  Rows: {sdata['max_row']}, Cols: {sdata['max_col']}")
        merged = sdata.get("merged_cells", [])
        if merged:
            print(f"  Merged cells: {merged[:10]}{'...' if len(merged)>10 else ''}")
        # Print first 10 rows
        for i, row in enumerate(sdata["rows"][:15]):
            vals = []
            for cell in row:
                v = cell["value"]
                if v is not None:
                    vals.append(f"{cell['col']}{cell['row']}={v}")
            if vals:
                print(f"    Row {i+1}: {' | '.join(vals)}")


def print_pdf_summary(r):
    print(f"  File: {r['relative_path']}")
    print(f"  Size: {r['size_kb']} KB")
    print(f"  Pages: {r.get('page_count', '?')}")
    print(f"  Is Scanned (no text): {r.get('is_scanned', '?')}")
    print(f"  Total text length: {r.get('total_text_length', 0)}")
    if "error" in r:
        print(f"  ERROR: {r['error']}")
        return
    for page in r.get("pages", []):
        print(f"\n  --- Page {page['page_num']} ---")
        print(f"  Text length: {page['text_length']}")
        print(f"  Tables detected: {page['table_count']}")
        if page["text_preview"] and page["text_preview"] != "(no text)":
            # Show first 800 chars
            preview = page["text_preview"][:1500].replace("\n", "\n    ")
            print(f"  Text preview:\n    {preview}")
        for ti, table in enumerate(page.get("tables", [])):
            print(f"\n  Table {ti+1} ({len(table)} rows):")
            for ri, row in enumerate(table[:8]):
                print(f"    Row {ri}: {row}")
            if len(table) > 8:
                print(f"    ... ({len(table) - 8} more rows)")


# Print summaries
for customer, results in data.items():
    print(f"\n{'#'*70}")
    print(f"# CUSTOMER: {customer}")
    print(f"{'#'*70}")
    for r in results:
        print(f"\n{'='*50}")
        if r["type"] == "excel":
            print_excel_summary(r)
        elif r["type"] == "pdf":
            print_pdf_summary(r)
        else:
            print(f"  Unknown: {r}")
        print()
