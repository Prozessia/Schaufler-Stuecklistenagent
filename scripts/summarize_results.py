"""Quick summary of mapping test results."""

import json
from pathlib import Path

results_path = Path("data/test_outputs/mapping_results.json")
data = json.loads(results_path.read_text(encoding="utf-8"))

print(f"Total customers tested: {len(data)}\n")
print(
    f"{'Customer':25s} {'Accuracy':>8s}  {'Correct':>7s}  {'Missing':>7s}  {'Wrong':>5s}  {'GT total':>8s}"
)
print("-" * 75)

total_correct = 0
total_gt = 0

for d in data:
    cust = d["customer"]
    acc = d["accuracy"]
    correct = d["correct"]
    missing = d["missing"]
    incorrect = d["incorrect"]
    gt_total = d["total_gt_mappings"]
    total_correct += correct
    total_gt += gt_total
    print(
        f"{cust:25s} {acc:>7.0%}  {correct:>7d}  {missing:>7d}  {incorrect:>5d}  {gt_total:>8d}"
    )

print("-" * 75)
overall = total_correct / total_gt if total_gt else 0
print(
    f"{'OVERALL':25s} {overall:>7.0%}  {total_correct:>7d}  {'':>7s}  {'':>5s}  {total_gt:>8d}"
)

# Show details for incorrect/missing mappings
print("\n\n=== ISSUES (incorrect or missing) ===\n")
for d in data:
    issues = [det for det in d.get("details", []) if det["status"] != "CORRECT"]
    if issues:
        print(f"--- {d['customer']} ---")
        for det in issues:
            print(
                f"  [{det['status']:8s}] GT: {det['gt_source']} -> {det['gt_target']}"
            )
            if det.get("actual_target"):
                print(
                    f"             Got: {det['gt_source']} -> {det['actual_target']} (conf={det.get('confidence', '?')})"
                )
        print()
