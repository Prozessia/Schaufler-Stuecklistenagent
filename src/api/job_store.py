"""Persistent job store backed by SQLite.

Replaces the previous in-memory store so job state survives API restarts.
SQLite is used (not Redis) because Redis is not part of the deployment stack
and SQLite ships with Python — no extra dependency, no extra container.

API surface (`create`, `get`, `update`, `list_all`) is unchanged so existing
routes keep working.

On startup, any job left in `pending` or `processing` is marked `failed`
with reason "Server restart" — running pipelines do not survive a crash and
the user must re-upload.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.scoring.audit_trail import BomAuditTrail

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_PATH = Path(
    os.getenv("BOM_MAPPER_JOB_DB_PATH", _PROJECT_ROOT / "data" / "jobs.db")
)


@dataclass
class Job:
    job_id: str
    filename: str
    filepath: Path
    customer: str = ""
    status: str = "pending"  # pending, processing, completed, failed
    progress: float = 0.0
    error: str | None = None
    audit: BomAuditTrail | None = None
    export_path: Path | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Phase 0 — denormalized summary (derived from the audit on every update so
    # the list view never has to parse audit_json). Zero/false until the job
    # completes (or is backfilled).
    total_rows: int = 0
    total_cells: int = 0
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    neutral_count: int = 0
    manual_confirmed_count: int = 0
    completeness_guaranteed: bool = False
    expected_position_count: int = 0
    archived: bool = False


_PERSISTED_FIELDS = {
    "job_id",
    "filename",
    "filepath",
    "customer",
    "status",
    "progress",
    "error",
    "audit",
    "export_path",
    "created_at",
    "updated_at",
}

# Denormalized columns derived from the audit (kept in sync inside `update`).
_SUMMARY_COLUMNS = (
    "total_rows",
    "total_cells",
    "green_count",
    "yellow_count",
    "red_count",
    "neutral_count",
    "manual_confirmed_count",
    "completeness_guaranteed",
    "expected_position_count",
)


def _summarize_audit(audit: BomAuditTrail) -> dict[str, int]:
    """Project an audit trail onto the denormalized summary columns.

    `total_rows` counts distinct, non-excluded BOM rows (matching what
    build_job_result renders); the rest mirror the audit's own counters.
    """
    excluded = set(getattr(audit, "excluded_rows", None) or [])
    rows = {cell.row_index for cell in audit.cells if cell.row_index not in excluded}
    return {
        "total_rows": len(rows),
        "total_cells": int(audit.total_cells),
        "green_count": int(audit.green_count),
        "yellow_count": int(audit.yellow_count),
        "red_count": int(audit.red_count),
        "neutral_count": int(audit.neutral_count),
        "manual_confirmed_count": int(audit.manual_confirmed_count),
        "completeness_guaranteed": 1 if audit.completeness_guaranteed else 0,
        "expected_position_count": int(audit.expected_position_count),
    }


def _as_int(value: object) -> int:
    return int(value) if value is not None else 0


class JobStore:
    """SQLite-backed job storage with the same API as the old in-memory store."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False because FastAPI workers may touch the
        # connection from different threads; the lock above serializes access.
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        self._conn.row_factory = sqlite3.Row
        # WAL keeps reads fast while a job writes its (large) audit blob, and a
        # busy_timeout avoids "database is locked" under concurrent access.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.DatabaseError as exc:  # noqa: BLE001
            logger.warning("Could not set SQLite PRAGMAs: %s", exc)
        self._init_schema()
        self._recover_orphaned_jobs()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id      TEXT PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    filepath    TEXT NOT NULL,
                    customer    TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    progress    REAL NOT NULL DEFAULT 0.0,
                    error       TEXT,
                    audit_json  TEXT,
                    export_path TEXT,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                )
                """)

            # Phase 0 — additive summary columns so the job list can be served
            # WITHOUT deserializing the (large) audit_json blob per row. ALTER
            # TABLE ADD COLUMN is a metadata-only change: it does not rewrite the
            # multi-GB table. Existing rows get NULL (backfill script fills them).
            existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(jobs)")}
            for column in _SUMMARY_COLUMNS:
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} INTEGER")

            # Phase 1 — soft delete. NULL = active; a timestamp = archived.
            if "deleted_at" not in existing:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN deleted_at REAL")

            # gzip-compressed audit (≈ 20× smaller than raw JSON). New writes go
            # here; audit_json is kept only as a legacy read fallback.
            if "audit_gz" not in existing:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN audit_gz BLOB")

            # Indices for the list view (sort by date, filter by status/customer).
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_customer ON jobs(customer)"
            )

    def _recover_orphaned_jobs(self) -> None:
        """Mark any in-flight jobs as failed — they did not survive the restart."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT job_id FROM jobs WHERE status IN ('pending', 'processing')"
            )
            orphaned = [r["job_id"] for r in cur.fetchall()]
            if not orphaned:
                return
            now = time.time()
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = 'Server restart — job did not survive process exit',
                    updated_at = ?
                WHERE status IN ('pending', 'processing')
                """,
                (now,),
            )
        logger.warning(
            "Recovered %d orphaned job(s) as failed after restart: %s",
            len(orphaned),
            orphaned,
        )

    def create(self, job_id: str, filename: str, filepath: Path) -> Job:
        now = time.time()
        job = Job(
            job_id=job_id,
            filename=filename,
            filepath=filepath,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    job_id, filename, filepath, customer, status, progress,
                    error, audit_json, export_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.filename,
                    str(job.filepath),
                    job.customer,
                    job.status,
                    job.progress,
                    job.error,
                    None,
                    None,
                    job.created_at,
                    job.updated_at,
                ),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
        return _row_to_job(row) if row else None

    def update(self, job_id: str, **kwargs) -> None:
        if not kwargs:
            return
        unknown = set(kwargs) - _PERSISTED_FIELDS
        if unknown:
            raise ValueError(f"Unknown job fields: {unknown}")

        sets: list[str] = []
        values: list[object] = []
        for k, v in kwargs.items():
            if k == "audit":
                # Store gzip-compressed; clear the legacy plaintext column.
                sets.append("audit_gz = ?")
                values.append(
                    gzip.compress(v.model_dump_json().encode("utf-8"), 5)
                    if v is not None
                    else None
                )
                sets.append("audit_json = ?")
                values.append(None)
            elif k == "export_path":
                sets.append("export_path = ?")
                values.append(str(v) if v is not None else None)
            elif k == "filepath":
                sets.append("filepath = ?")
                values.append(str(v))
            else:
                sets.append(f"{k} = ?")
                values.append(v)

        # Keep the denormalized summary columns in sync whenever the audit
        # changes (completion, cell edits, row exclusions all flow through here).
        audit_obj = kwargs.get("audit")
        if audit_obj is not None:
            for column, summary_value in _summarize_audit(audit_obj).items():
                sets.append(f"{column} = ?")
                values.append(summary_value)

        sets.append("updated_at = ?")
        values.append(time.time())
        values.append(job_id)

        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?",
                values,
            )

    def list_all(self) -> list[Job]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [_row_to_job(r) for r in rows]

    def list_summaries(
        self,
        *,
        include_archived: bool = False,
        status: str | None = None,
        query: str | None = None,
    ) -> list[Job]:
        """List jobs WITHOUT loading audit_json — cheap, for the list view.

        Returns Job objects with ``audit=None`` and the denormalized counters
        populated from the summary columns. Archived (soft-deleted) jobs are
        excluded unless ``include_archived`` is set.
        """
        clauses: list[str] = []
        params: list[object] = []
        if not include_archived:
            clauses.append("deleted_at IS NULL")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("(filename LIKE ? OR customer LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._lock:
            cur = self._conn.execute(
                f"SELECT {_SUMMARY_SELECT} FROM jobs{where} ORDER BY created_at DESC",
                params,
            )
            rows = cur.fetchall()
        return [_row_to_summary(r) for r in rows]

    def get_summary(self, job_id: str) -> Job | None:
        """Fetch one job WITHOUT parsing audit_json (audit stays None)."""
        with self._lock:
            cur = self._conn.execute(
                f"SELECT {_SUMMARY_SELECT} FROM jobs WHERE job_id = ?", (job_id,)
            )
            row = cur.fetchone()
        return _row_to_summary(row) if row else None

    def set_deleted(self, job_id: str, deleted: bool = True) -> None:
        """Soft-delete (archive) or restore a job."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET deleted_at = ?, updated_at = ? WHERE job_id = ?",
                (now if deleted else None, now, job_id),
            )

    def purge(self, job_id: str) -> Job | None:
        """Permanently remove a job row. Returns the job summary (for file
        cleanup) or None if it did not exist. Does not parse audit_json."""
        job = self.get_summary(job_id)
        if job is None:
            return None
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        return job

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_job(row: sqlite3.Row) -> Job:
    audit: BomAuditTrail | None = None
    keys = row.keys()
    audit_gz = row["audit_gz"] if "audit_gz" in keys else None
    raw_json: str | None = None
    if audit_gz:
        try:
            raw_json = gzip.decompress(audit_gz).decode("utf-8")
        except (OSError, ValueError) as e:
            logger.warning("Could not gunzip audit for %s: %s", row["job_id"], e)
    elif row["audit_json"]:
        raw_json = row["audit_json"]

    if raw_json:
        try:
            audit = BomAuditTrail.model_validate_json(raw_json)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("Could not deserialize audit for %s: %s", row["job_id"], e)

    export_path = Path(row["export_path"]) if row["export_path"] else None

    return Job(
        job_id=row["job_id"],
        filename=row["filename"],
        filepath=Path(row["filepath"]),
        customer=row["customer"] or "",
        status=row["status"],
        progress=float(row["progress"]),
        error=row["error"],
        audit=audit,
        export_path=export_path,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        total_rows=_as_int(row["total_rows"]),
        total_cells=_as_int(row["total_cells"]),
        green_count=_as_int(row["green_count"]),
        yellow_count=_as_int(row["yellow_count"]),
        red_count=_as_int(row["red_count"]),
        neutral_count=_as_int(row["neutral_count"]),
        manual_confirmed_count=_as_int(row["manual_confirmed_count"]),
        completeness_guaranteed=bool(row["completeness_guaranteed"]),
        expected_position_count=_as_int(row["expected_position_count"]),
        archived=row["deleted_at"] is not None,
    )


# Scalar columns for the list view — deliberately excludes audit_json so the
# multi-GB blobs are never read or parsed when listing jobs.
_SUMMARY_SELECT = (
    "job_id, filename, filepath, customer, status, progress, error, "
    "export_path, created_at, updated_at, deleted_at, " + ", ".join(_SUMMARY_COLUMNS)
)


def _row_to_summary(row: sqlite3.Row) -> Job:
    """Build a Job without touching audit_json (audit stays None)."""
    return Job(
        job_id=row["job_id"],
        filename=row["filename"],
        filepath=Path(row["filepath"]),
        customer=row["customer"] or "",
        status=row["status"],
        progress=float(row["progress"]),
        error=row["error"],
        audit=None,
        export_path=Path(row["export_path"]) if row["export_path"] else None,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        total_rows=_as_int(row["total_rows"]),
        total_cells=_as_int(row["total_cells"]),
        green_count=_as_int(row["green_count"]),
        yellow_count=_as_int(row["yellow_count"]),
        red_count=_as_int(row["red_count"]),
        neutral_count=_as_int(row["neutral_count"]),
        manual_confirmed_count=_as_int(row["manual_confirmed_count"]),
        completeness_guaranteed=bool(row["completeness_guaranteed"]),
        expected_position_count=_as_int(row["expected_position_count"]),
        archived=row["deleted_at"] is not None,
    )


# Singleton — initialized at import time so existing imports keep working.
job_store = JobStore()
