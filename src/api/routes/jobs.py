"""Jobs routes — status, results, and export download."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from src.api.job_queue import job_queue
from src.api.job_store import Job, job_store
from src.api.models.schemas import (
    JobResult,
    JobStatus,
    JobSummary,
)
from src.api.result_builder import build_job_result

logger = logging.getLogger(__name__)

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_UPLOAD_DIR = (_PROJECT_ROOT / "data" / "uploads").resolve()
_EXPORT_DIR = (_PROJECT_ROOT / "data" / "exports").resolve()


def _job_to_summary(j: Job) -> JobSummary:
    return JobSummary(
        job_id=j.job_id,
        status=j.status,
        filename=j.filename,
        customer=j.customer,
        progress=j.progress,
        error=j.error,
        created_at=j.created_at,
        updated_at=j.updated_at,
        total_rows=j.total_rows,
        total_cells=j.total_cells,
        green_count=j.green_count,
        yellow_count=j.yellow_count,
        red_count=j.red_count,
        neutral_count=j.neutral_count,
        manual_confirmed_count=j.manual_confirmed_count,
        completeness_guaranteed=j.completeness_guaranteed,
        expected_position_count=j.expected_position_count,
        archived=j.archived,
    )


def _safe_unlink(path: Path | None, allowed_dir: Path) -> None:
    """Delete a file only if it lives inside the allowed directory."""
    if not path:
        return
    try:
        resolved = Path(path).resolve()
        if allowed_dir in resolved.parents and resolved.is_file():
            resolved.unlink()
    except OSError as exc:  # noqa: BLE001
        logger.warning("Could not delete %s: %s", path, exc)


@router.get("/jobs", response_model=list[JobSummary])
async def list_jobs(
    include_archived: bool = Query(False),
    status: str | None = Query(None),
    q: str | None = Query(None),
):
    """List jobs with denormalized counters (no audit parsing — Phase 0).

    Active jobs only by default; `include_archived=true` adds soft-deleted ones.
    Optional `status` and `q` (filename/customer substring) filters.
    """
    return [
        _job_to_summary(j)
        for j in job_store.list_summaries(
            include_archived=include_archived, status=status, query=q
        )
    ]


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, purge: bool = Query(False)):
    """Archive a job (soft delete, reversible) or permanently purge it.

    `purge=true` removes the DB row and the associated upload/export files —
    irreversible. The default soft delete only hides it from the active list.
    """
    job = job_store.get_summary(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if purge:
        purged = job_store.purge(job_id)
        if purged:
            _safe_unlink(purged.filepath, _UPLOAD_DIR)
            _safe_unlink(purged.export_path, _EXPORT_DIR)
        return {"ok": True, "purged": True, "job_id": job_id}

    job_store.set_deleted(job_id, deleted=True)
    return {"ok": True, "archived": True, "job_id": job_id}


@router.post("/jobs/{job_id}/restore", response_model=JobSummary)
async def restore_job(job_id: str):
    """Restore a soft-deleted (archived) job back to the active list."""
    job = job_store.get_summary(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job_store.set_deleted(job_id, deleted=False)
    restored = job_store.get_summary(job_id)
    return _job_to_summary(restored or job)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    """Get status of a specific job."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    queue_position: int | None = None
    if job.status == "pending":
        queue_position = job_queue.position(job_id)
    return JobStatus(
        job_id=job.job_id,
        status=job.status,
        filename=job.filename,
        customer=job.customer,
        progress=job.progress,
        error=job.error,
        queue_position=queue_position,
    )


@router.post("/jobs/{job_id}/retry", response_model=JobStatus)
async def retry_job(job_id: str):
    """Re-queue a failed job for processing.

    Returns 404 if the job does not exist, 409 if it is not in 'failed' state,
    410 if the source file was already deleted.
    """
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"Job kann nur im Status 'failed' neu gestartet werden (aktuell: '{job.status}').",
        )
    if not job.filepath.exists():
        raise HTTPException(
            status_code=410,
            detail="Quelldatei wurde bereits gelöscht — bitte erneut hochladen.",
        )
    job_store.update(job_id, status="pending", progress=0.0, error=None)
    await job_queue.submit(job_id)
    return JobStatus(
        job_id=job_id,
        status="pending",
        filename=job.filename,
        customer=job.customer,
        progress=0.0,
        error=None,
        queue_position=job_queue.position(job_id),
    )


@router.get("/jobs/{job_id}/result", response_model=JobResult)
async def get_job_result(job_id: str):
    """Get the full result with cells and traffic-light classification."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(
            status_code=409, detail=f"Job status is '{job.status}', not 'completed'"
        )
    if not job.audit:
        raise HTTPException(status_code=500, detail="No audit data available")

    return build_job_result(job_id, job)


@router.get("/jobs/{job_id}/export")
async def download_export(job_id: str):
    """Download the exported Excel file."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(
            status_code=409, detail=f"Job not completed yet (status: {job.status})"
        )
    if not job.export_path or not job.export_path.exists():
        raise HTTPException(status_code=500, detail="Export file not found")

    return FileResponse(
        path=job.export_path,
        filename=f"{job.customer or 'export'}_schaufler.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/jobs/{job_id}/source")
async def download_source(job_id: str):
    """Stream the original uploaded source file for side-by-side review."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    source_path = job.filepath.resolve()
    if _UPLOAD_DIR not in source_path.parents:
        raise HTTPException(status_code=403, detail="Source file path not allowed")
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")

    media_type, _ = mimetypes.guess_type(job.filename or source_path.name)

    # Serve INLINE (not as a download). FileResponse(filename=...) defaults to
    # Content-Disposition: attachment, which makes the browser download the file
    # and leaves the iframe/PDF viewer blank. inline lets it render in place.
    return FileResponse(
        path=source_path,
        media_type=media_type or "application/octet-stream",
        content_disposition_type="inline",
    )
