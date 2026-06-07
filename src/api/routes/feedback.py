"""Feedback route — submit corrections for learning."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.cell_edits import apply_cell_edits, apply_row_exclusions
from src.api.job_store import job_store
from src.api.models.schemas import (
    CellEditRequest,
    FeedbackRequest,
    JobResult,
    RowExclusionRequest,
)
from src.api.result_builder import build_job_result
from src.export.excel_exporter import export_to_excel
from src.export.feedback_store import Correction, FeedbackStore

router = APIRouter()

_feedback_store = FeedbackStore()


def _regenerate_export(job) -> None:
    """Rebuild the downloadable Excel so it reflects the current audit state.

    Manual cell edits and row exclusions both mutate ``job.audit``; the export
    served by ``GET /jobs/{id}/export`` is a file, so it must be rewritten or the
    download would be stale. The zero-data-loss guard runs inside export_to_excel.
    """
    if not job.export_path or not job.audit:
        return
    export_to_excel(
        job.audit,
        job.export_path,
        colour_cells=True,
        add_audit_sheet=True,
        meta={"customer": job.audit.customer},
    )


def _build_correction_records(
    job,
    corrections: list[FeedbackRequest | CellEditRequest],
) -> list[Correction]:
    stored: list[Correction] = []
    audit_lookup = {}
    if job.audit:
        audit_lookup = {
            (cell.row_index, cell.target_field): cell for cell in job.audit.cells
        }

    for correction in corrections:
        original_cell = audit_lookup.get(
            (correction.row_index, correction.target_field)
        )
        stored.append(
            Correction(
                customer=job.customer,
                source_file=job.filename,
                row_index=correction.row_index,
                target_field=correction.target_field,
                target_column=original_cell.target_column if original_cell else "",
                raw_value=original_cell.raw_value if original_cell else None,
                original_transformed=(
                    original_cell.transformed_value if original_cell else None
                ),
                corrected_value=correction.corrected_value,
                original_score=original_cell.final_score if original_cell else 0.0,
                original_classification=(
                    original_cell.classification.value if original_cell else ""
                ),
                correction_type=getattr(correction, "correction_type", "value"),
            )
        )

    return stored


@router.post("/jobs/{job_id}/feedback")
async def submit_feedback(job_id: str, corrections: list[FeedbackRequest]):
    """Submit user corrections for a completed job."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Job not completed yet")

    stored = _build_correction_records(job, corrections)
    _feedback_store.add_corrections(stored)

    return {
        "message": f"Saved {len(stored)} corrections",
        "total_corrections": _feedback_store.correction_count(),
    }


@router.patch("/jobs/{job_id}/cells", response_model=JobResult)
async def update_cells(job_id: str, edits: list[CellEditRequest]):
    """Persist browser cell edits back into the export-relevant audit model."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Job not completed yet")
    if not job.audit:
        raise HTTPException(status_code=500, detail="No audit data available")
    if not edits:
        return build_job_result(job_id, job)

    stored = _build_correction_records(job, edits)

    try:
        apply_cell_edits(job.audit, edits)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _feedback_store.add_corrections(stored)
    _regenerate_export(job)
    job_store.update(job_id, audit=job.audit)

    return build_job_result(job_id, job)


@router.patch("/jobs/{job_id}/rows", response_model=JobResult)
async def update_row_exclusions(job_id: str, request: RowExclusionRequest):
    """Exclude (or restore) reviewer-selected rows from a completed job result.

    Exclusion is an explicit, audited reviewer action — the rows disappear from the
    grid and the export, and the zero-data-loss guard accepts the removal as
    deliberate (not silent loss). Restoring (`excluded=false`) reverses it.
    """
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Job not completed yet")
    if not job.audit:
        raise HTTPException(status_code=500, detail="No audit data available")
    if not request.row_indices:
        return build_job_result(job_id, job)

    apply_row_exclusions(
        job.audit,
        request.row_indices,
        excluded=request.excluded,
        reason=request.reason,
    )
    _regenerate_export(job)
    job_store.update(job_id, audit=job.audit)

    return build_job_result(job_id, job)
