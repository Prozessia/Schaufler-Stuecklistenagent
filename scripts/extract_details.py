"""Extract detailed info from raw_analysis.json for report generation."""

import json
import os
import sys

BASE = r"c:\Users\douioui\Documents\privat\Schaufler-Stücklistenagent"
OUT = os.path.join(BASE, "data", "analysis", "detailed_output.txt")

with open(
    os.path.join(BASE, "data", "analysis", "raw_analysis.json"), "r", encoding="utf-8"
) as f:
    data = json.load(f)

# Redirect stdout to file
sys.stdout = open(OUT, "w", encoding="utf-8")

# ============================================================
# PART 1: Target Template Analysis (CadCam Vorlage)
# ============================================================
print("=" * 70)
print("PART 1: TARGET TEMPLATE (CadCam_Stuecklistenvorlage V191)")
print("=" * 70)

# Collect ALL target templates to find the most complete one (most rows)
best_template = None
best_rows = 0
for cust, files in data.items():
    for f in files:
        if f.get("is_target_template") and f["type"] == "excel":
            for sname, sdata in f.get("sheets_data", {}).items():
                mr = sdata.get("max_row", 0) or 0
                if mr > best_rows:
                    best_rows = mr
                    best_template = (cust, f, sname, sdata)

if best_template:
    cust, f, sname, sdata = best_template
    print(f"\nBest template from: {cust}")
    print(f"File: {f['file']}")
    print(f"Sheet: {sname}")
    print(f"Rows: {sdata['max_row']}, Cols: {sdata['max_col']}")
    print(f"Merged cells count: {len(sdata.get('merged_cells', []))}")
    print(f"Merged cells: {sdata.get('merged_cells', [])[:15]}")
    print("\nAll rows with data:")
    for i, row in enumerate(sdata["rows"][:50]):
        vals = []
        for c in row:
            v = c["value"]
            if v is not None and v != "(merged)":
                vals.append(f"{c['col']}{c['row']}={v}")
        if vals:
            print(f"  Row {i+1}: {' | '.join(vals)}")

# Also check ALL unique templates for sheet names and column structures
print("\n\nAll target templates found:")
for cust, files in data.items():
    for f in files:
        if f.get("is_target_template") and f["type"] == "excel":
            sheets_info = []
            for sname, sdata in f.get("sheets_data", {}).items():
                sheets_info.append(
                    f"{sname}(rows={sdata['max_row']},cols={sdata['max_col']})"
                )
            print(f"  {cust}: {f['file']} -> {', '.join(sheets_info)}")

# ============================================================
# PART 2: Customer BOM Analysis (PDF + non-template Excel)
# ============================================================
print("\n\n" + "=" * 70)
print("PART 2: CUSTOMER BOMs")
print("=" * 70)

for cust, files in data.items():
    for f in files:
        if f.get("is_target_template"):
            continue
        print(f"\n{'~' * 60}")
        print(f"CUSTOMER: {cust}")
        print(f"FILE: {f['file']}")
        print(f"PATH: {f['relative_path']}")
        print(f"TYPE: {f['type']}, SIZE: {f['size_kb']} KB")

        if f["type"] == "pdf":
            print(f"PAGES: {f.get('page_count', '?')}")
            print(f"SCANNED: {f.get('is_scanned', '?')}")
            print(f"TEXT LENGTH: {f.get('total_text_length', 0)}")
            print(f"TOTAL IMAGES: {f.get('total_images', 0)}")

            # Print first 3 pages text and tables
            for page in f.get("pages", [])[:4]:
                pn = page["page_num"]
                print(f"\n  --- Page {pn} ---")
                print(
                    f"  Text len: {page['text_length']}, Tables: {page['table_count']}, Images: {page['image_count']}"
                )
                if page["text_preview"] != "(no text)":
                    preview = page["text_preview"][:1200]
                    print(f"  Text preview:\n{preview}")
                for ti, table in enumerate(page.get("tables", [])):
                    print(f"\n  Table {ti+1} on page {pn} ({len(table)} rows):")
                    for ri, row in enumerate(table[:10]):
                        print(f"    {ri}: {row}")
                    if len(table) > 10:
                        print(f"    ... ({len(table)-10} more rows)")

        elif f["type"] == "excel":
            print(f"SHEETS: {f.get('sheets', [])}")
            for sname, sdata in f.get("sheets_data", {}).items():
                print(f"\n  --- Sheet: '{sname}' ---")
                print(f"  Rows: {sdata['max_row']}, Cols: {sdata['max_col']}")
                merged = sdata.get("merged_cells", [])
                if merged:
                    print(f"  Merged: {merged[:10]}")
                for i, row in enumerate(sdata["rows"][:15]):
                    vals = []
                    for c in row:
                        v = c["value"]
                        if v is not None and v != "(merged)":
                            vals.append(f"{c['col']}{c['row']}={v}")
                    if vals:
                        print(f"  Row {i+1}: {' | '.join(vals)}")
