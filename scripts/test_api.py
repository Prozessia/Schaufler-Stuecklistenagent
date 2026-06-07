"""Quick test: upload a file to the API and poll for results."""
import http.client
import json
import time
from pathlib import Path

# Find a test file
test_dir = Path("data/input/PDF_POC/GF")
test_file = next(test_dir.glob("*"), None)
if not test_file:
    print("No test file found")
    exit(1)

print(f"Uploading: {test_file.name}")

# Upload
boundary = "------bom-test-boundary"
file_data = test_file.read_bytes()
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{test_file.name}"\r\n'
    f"Content-Type: application/pdf\r\n"
    f"\r\n"
).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

conn = http.client.HTTPConnection("localhost", 8000)
conn.request("POST", "/upload", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
resp = conn.getresponse()
data = json.loads(resp.read().decode())
print(f"Upload response ({resp.status}): {json.dumps(data, indent=2)}")

if resp.status != 200:
    exit(1)

job_id = data["job_id"]

# Poll status
for i in range(30):
    time.sleep(2)
    conn = http.client.HTTPConnection("localhost", 8000)
    conn.request("GET", f"/jobs/{job_id}")
    resp = conn.getresponse()
    status = json.loads(resp.read().decode())
    print(f"  [{i+1}] Status: {status['status']}  Progress: {status['progress']}  Error: {status.get('error')}")
    if status["status"] in ("completed", "failed"):
        break

# Try to get result
if status["status"] == "completed":
    conn = http.client.HTTPConnection("localhost", 8000)
    conn.request("GET", f"/jobs/{job_id}/result")
    resp = conn.getresponse()
    result = json.loads(resp.read().decode())
    print(f"\nResult: {result['total_rows']} rows, {result['total_cells']} cells")
    print(f"  Green: {result['green_count']} ({result['green_pct']}%)")
    print(f"  Yellow: {result['yellow_count']} ({result['yellow_pct']}%)")
    print(f"  Red: {result['red_count']} ({result['red_pct']}%)")
else:
    print(f"\nJob failed: {status.get('error')}")
