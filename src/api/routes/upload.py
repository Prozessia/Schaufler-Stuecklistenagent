"""Upload route — POST /upload to submit a BOM file for processing."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File, HTTPException

from src.api.job_queue import job_queue
from src.api.job_store import job_store
from src.api.models.schemas import UploadResponse
from src.core.rate_limit import RateLimiter

router = APIRouter()

# SEC-004: 30 uploads per IP per 60 s (Azure cost protection)
_upload_rate_limiter = RateLimiter(max_attempts=30, window_seconds=60)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR = _PROJECT_ROOT / "data" / "uploads"

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xlsm", ".csv"}
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
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    customer: str = Form(""),
):
    """Upload a BOM file and start processing."""
    # Validate optional customer field (trim, max 100 chars)
    customer = customer.strip()
    if len(customer) > 100:
        raise HTTPException(
            status_code=400,
            detail="Kundenname darf maximal 100 Zeichen lang sein.",
        )

    # SEC-004: per-IP rate limit
    client_ip = (request.client.host if request.client else None) or "unknown"
    if not _upload_rate_limiter.allow(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Zu viele Upload-Anfragen — bitte warten.",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext == ".xls":
        raise HTTPException(
            status_code=400,
            detail=(
                "Legacy-Format .xls wird nicht unterstützt — "
                "bitte als .xlsx speichern und erneut hochladen."
            ),
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Read file content
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # SEC-005: magic-byte validation
    if ext == ".pdf":
        if content[:5] != b"%PDF-":
            raise HTTPException(
                status_code=400,
                detail=f"Dateiinhalt entspricht nicht dem Format '{ext}'.",
            )
    elif ext in {".xlsx", ".xlsm"}:
        if content[:4] != b"PK\x03\x04":
            raise HTTPException(
                status_code=400,
                detail=f"Dateiinhalt entspricht nicht dem Format '{ext}'.",
            )
    elif ext == ".csv":
        if b"\x00" in content[:1024]:
            raise HTTPException(
                status_code=400,
                detail=f"Dateiinhalt entspricht nicht dem Format '{ext}'.",
            )

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
    job_store.create(job_id, file.filename, filepath, customer=customer)
    await job_queue.submit(job_id)

    return UploadResponse(job_id=job_id, filename=file.filename)
