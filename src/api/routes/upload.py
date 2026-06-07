"""Upload route — POST /upload to submit a BOM file for processing."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

from src.api.job_queue import job_queue
from src.api.job_store import job_store
from src.api.models.schemas import UploadResponse

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR = _PROJECT_ROOT / "data" / "uploads"

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _safe_filename(filename: str) -> str:
    """Strip any path component from a client-supplied filename (SEC-2).

    Handles both POSIX and Windows separators so a name like
    ``..\\..\\etc\\passwd`` or ``../../secret`` cannot escape UPLOAD_DIR.
    Returns "" for names that reduce to a pure path reference ("." / "..").
    """
    name = Path(filename.replace("\\", "/")).name
    return "" if name in {".", ".."} else name


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """Upload a BOM file and start processing."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read file content
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Save to disk (SEC-2: never trust the client filename for the on-disk path)
    job_id = uuid.uuid4().hex[:12]
    safe_name = _safe_filename(file.filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filepath = UPLOAD_DIR / f"{job_id}_{safe_name}"
    filepath.write_bytes(content)

    # Create job and enqueue. The bounded queue + worker pool caps concurrent
    # Azure load globally (no fire-and-forget task that could be GC'd, AW-1).
    # The original (display) filename is kept for the UI; only the on-disk path
    # is sanitised.
    job_store.create(job_id, file.filename, filepath)
    await job_queue.submit(job_id)

    return UploadResponse(job_id=job_id, filename=file.filename)
