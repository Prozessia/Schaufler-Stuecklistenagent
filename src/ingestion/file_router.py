"""File Router — detect file type and route to the appropriate parser."""

from __future__ import annotations

from pathlib import Path

from src.core.models import FileFormat

# Map file extensions to formats
_EXTENSION_MAP: dict[str, FileFormat] = {
    ".xlsx": FileFormat.EXCEL,
    ".xls": FileFormat.EXCEL,
    ".xlsm": FileFormat.EXCEL,
    ".csv": FileFormat.CSV,
    ".pdf": FileFormat.PDF,
}


def detect_format(filepath: Path | str) -> FileFormat:
    """Detect file format based on extension and magic bytes."""
    filepath = Path(filepath)

    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    # Extension-based detection
    ext = filepath.suffix.lower()
    fmt = _EXTENSION_MAP.get(ext, FileFormat.UNKNOWN)

    if fmt != FileFormat.UNKNOWN:
        return fmt

    # Magic-byte fallback for ambiguous extensions
    header = filepath.read_bytes()[:8]

    # XLSX/ZIP magic bytes
    if header[:4] == b"PK\x03\x04":
        return FileFormat.EXCEL

    # PDF magic bytes
    if header[:5] == b"%PDF-":
        return FileFormat.PDF

    return FileFormat.UNKNOWN


def infer_customer(filepath: Path | str) -> str:
    """Infer customer name from the directory structure.

    Expects: .../PDF_POC/<customer>/... or .../input/<customer>/...
    Checks more-specific markers first to avoid false matches.
    """
    filepath = Path(filepath).resolve()
    parts = filepath.parts

    # Check most-specific marker first
    for marker in ("PDF_POC", "input"):
        for i, part in enumerate(parts):
            if part == marker and i + 1 < len(parts):
                candidate = parts[i + 1]
                # Skip if the candidate is a known sub-marker
                if candidate in ("PDF_POC",):
                    continue
                return candidate

    # No customer folder in the path. Uploaded files live in generic working
    # directories (data/uploads, …) whose name is NOT a customer — return ""
    # so the pipeline doesn't surface "uploads" as the customer.
    parent = filepath.parent.name.lower()
    if parent in {"uploads", "input", "data", "exports", "output", "test_outputs", ""}:
        return ""
    return filepath.parent.name
