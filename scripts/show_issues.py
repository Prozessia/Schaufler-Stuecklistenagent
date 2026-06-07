"""Show detailed mappings for specific failing customers."""
import json, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

data = json.loads(Path("data/test_outputs/mapping_results.json").read_text(encoding="utf-8"))

for d in data:
    cust = d.get("customer", "?")
    if cust in ("audi", "ZF", "Ford", "Magna"):
        print(f"\n=== {cust} ===")
        for det in d.get("details", []):
            if det["status"] != "CORRECT":
                print(f"  [{det['status']}] gt_src={det['gt_source']} gt_tgt={det['gt_target']} actual_tgt={det.get('actual_target')} conf={det.get('confidence')}")
