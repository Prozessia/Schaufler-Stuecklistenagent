"""Shared PDF helpers used by both vision and legacy parsers."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


class ExtractionError(Exception):
    """Raised when PDF extraction fails irrecoverably."""


class PasswordProtectedPdfError(ExtractionError):
    """Raised when a PDF requires a password and cannot be processed."""


def _looks_like_password_error(message: str) -> bool:
    lowered = message.lower()
    return "password" in lowered or "encrypted" in lowered or "needs pass" in lowered


def open_pdf_document(filepath: Path | str) -> fitz.Document:
    """Open a PDF and fail fast on password-protected documents."""
    path = Path(filepath)

    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).strip()
        if _looks_like_password_error(message):
            raise PasswordProtectedPdfError(
                f"PDF is password-protected: {path.name}"
            ) from exc
        raise ExtractionError(f"Could not open PDF '{path.name}': {message}") from exc

    if bool(getattr(doc, "needs_pass", False)) or bool(
        getattr(doc, "is_encrypted", False)
    ):
        doc.close()
        raise PasswordProtectedPdfError(f"PDF is password-protected: {path.name}")

    return doc
