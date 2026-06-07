"""Extract Stammdaten sheets from the target template."""

import json, os

BASE = r"c:\Users\douioui\Documents\privat\Schaufler-Stücklistenagent"
with open(
    os.path.join(BASE, "data", "analysis", "raw_analysis.json"), "r", encoding="utf-8"
) as f:
    data = json.load(f)

for fdata in data["FCA"]:
    if fdata.get("is_target_template"):
        for sname in ["Stammdaten", "Stammdaten_ZK_Block_Detail"]:
            sdata = fdata["sheets_data"].get(sname, {})
            print(
                f"Sheet: {sname}, Rows={sdata.get('max_row')}, Cols={sdata.get('max_col')}"
            )
            for i, row in enumerate(sdata.get("rows", [])[:20]):
                vals = [
                    c
                    for c in row
                    if c["value"] is not None and c["value"] != "(merged)"
                ]
                if vals:
                    items = [(c["col"], c["value"]) for c in vals]
                    print(f"  Row {i+1}: {items}")
        break
