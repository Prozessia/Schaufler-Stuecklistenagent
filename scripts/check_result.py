"""Check the dimension cells from the latest completed job."""
import urllib.request
import json

r = urllib.request.urlopen("http://localhost:8000/jobs/464bcb19/result")
data = json.loads(r.read())
rows = data.get("rows", [])
print(f"Total rows: {len(rows)}")

for row in rows[:5]:
    ridx = row.get("row_index", "?")
    print(f"\n--- Row {ridx} ---")
    for cell in row.get("cells", []):
        tf = cell.get("target_field", "")
        tv = cell.get("transformed_value", "")
        rv = cell.get("raw_value", "")
        cls = cell.get("classification", "")
        score = cell.get("final_score", 0)
        src = cell.get("source_column", "")
        # Show all cells, highlight dimensions
        marker = " <<<" if ("dimension" in tf.lower() or "abma" in tf.lower()) else ""
        print(f"  {tf:25s} = {str(tv):30s} [{cls:6s}] score={score:.2f} (raw: {rv}){marker}")
