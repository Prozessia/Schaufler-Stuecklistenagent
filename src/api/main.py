"""FastAPI application — BOM-Mapper API.

Single-instance deployment (no multi-tenant).
Jobs are stored in-memory with results on disk.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before any module that reads env vars
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

os.environ["PYMUPDF_MESSAGE"] = "path:" + os.devnull

from src.api.routes import auth, upload, jobs, feedback, stats, settings
from src.core.auth import init_auth, verify_api_key

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = _PROJECT_ROOT / "data" / "uploads"
EXPORT_DIR = _PROJECT_ROOT / "data" / "exports"

_cors_raw = os.environ.get("CORS_ORIGINS", "").strip()
CORS_ORIGINS = (
    [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
    if _cors_raw
    else ["http://localhost:3000", "http://localhost:3001"]
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create directories and start the job-queue worker pool."""
    from src.api.job_queue import job_queue

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    init_auth()
    job_queue.start()
    logger.info("BOM-Mapper API started")
    yield
    await job_queue.stop()
    logger.info("BOM-Mapper API shutting down")


app = FastAPI(
    title="BOM-Mapper API",
    description="KI-gestütztes Stücklisten-Mapping für Schaufler Tooling",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, tags=["auth"])
app.include_router(upload.router, tags=["upload"])
app.include_router(jobs.router, tags=["jobs"])
app.include_router(feedback.router, tags=["feedback"])
app.include_router(stats.router, tags=["stats"])
app.include_router(settings.router, tags=["settings"])


@app.get("/health")
async def health():
    return {"status": "ok"}
