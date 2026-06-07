"""One-time backfill of the Phase-0 job summary columns.

The summary columns (total_rows, *_count, completeness_guaranteed, ...) are
filled automatically for every job that completes or is edited from now on.
This script populates them for jobs that already existed before the migration.

It streams one audit blob at a time (the jobs DB can be multiple GB), so memory
stays flat regardless of table size.

Usage:
    python scripts/backfill_job_summaries.py            # fill missing only
    python scripts/backfill_job_summaries.py --all       # recompute every job
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from src.api.job_store import _DEFAULT_DB_PATH, _SUMMARY_COLUMNS, _summarize_audit  # noqa: E402
from src.scoring.audit_trail import BomAuditTrail  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill job summary columns.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Recompute summaries for every job (default: only rows missing them).",
    )
    parser.add_argument("--db", default=str(_DEFAULT_DB_PATH), help="Path to jobs.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, isolation_level=None)
    conn.row_factory = sqlite3.Row

    if args.all:
        where = "audit_json IS NOT NULL"
    else:
        where = "audit_json IS NOT NULL AND total_cells IS NULL"

    job_ids = [r["job_id"] for r in conn.execute(f"SELECT job_id FROM jobs WHERE {where}")]
    total = len(job_ids)
    print(f"Backfilling {total} job(s) from {args.db} ...")

    set_clause = ", ".join(f"{col} = ?" for col in _SUMMARY_COLUMNS)
    done = 0
    skipped = 0
    start = time.time()

    for job_id in job_ids:
        row = conn.execute(
            "SELECT audit_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row or not row["audit_json"]:
            skipped += 1
            continue
        try:
            audit = BomAuditTrail.model_validate_json(row["audit_json"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {job_id}: could not parse audit ({exc}) — skipped")
            skipped += 1
            continue

        summary = _summarize_audit(audit)
        values = [summary[col] for col in _SUMMARY_COLUMNS]
        values.append(job_id)
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values)
        done += 1
        if done % 50 == 0:
            print(f"  ... {done}/{total}")

    conn.close()
    print(
        f"Done: {done} updated, {skipped} skipped in {time.time() - start:.1f}s. "
        "Consider running VACUUM separately if you want to shrink the DB."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
