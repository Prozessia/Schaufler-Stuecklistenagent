"""Quick test runner — saves results to test_results.txt."""

import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_ingestion/test_parse_all.py",
        "--tb=short",
        "-q",
    ],
    capture_output=True,
    text=True,
    timeout=300,
)

with open("test_results.txt", "w", encoding="utf-8") as f:
    f.write(result.stdout)
    if result.stderr:
        f.write("\n--- STDERR ---\n")
        f.write(result.stderr)
    f.write(f"\n--- RETURN CODE: {result.returncode} ---\n")

print(f"Done. Return code: {result.returncode}")
print(f"Output saved to test_results.txt ({len(result.stdout)} chars)")
