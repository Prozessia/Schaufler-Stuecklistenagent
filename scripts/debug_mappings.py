"""Debug: show ALL mappings (not just incorrect) for specific customers."""
import json, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

data = json.loads(Path("data/test_outputs/mapping_results.json").read_text(encoding="utf-8"))

# Try reading the full mapping data from stdout (mappings aren't in the eval JSON)
# Actually map_results.json only has eval data. Let me check if we store full mappings.

# The test script doesn't store the full mappings, only the eval.
# Let me check the raw stdout for the mapping details.

# Actually let me parse the stdout file for these specific customers
out = Path("data/test_outputs/mapping_run2_stdout.txt").read_text(encoding="utf-8", errors="replace")

for cust in ["audi", "Ford", "ZF"]:
    marker = f"CUSTOMER: {cust}"
    idx = out.find(marker)
    if idx < 0:
        continue
    # Get next 3000 chars after the marker
    chunk = out[idx:idx+3000]
    print(f"\n{'='*60}")
    print(chunk[:2000])
