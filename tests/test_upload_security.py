"""Sprint 1 — SEC-2 (filename sanitisation).

AW-1 (background-task reference) is now covered by the job queue owning its
worker tasks for the app lifetime; see test_job_queue.py.
"""

from __future__ import annotations

import pytest

from src.api.routes import upload


# --- SEC-2: filename sanitisation -----------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("normal.pdf", "normal.pdf"),
        ("../../etc/passwd", "passwd"),
        (r"..\..\windows\system32\evil.xlsx", "evil.xlsx"),
        ("sub/dir/file.csv", "file.csv"),
        (r"C:\abs\path\bom.pdf", "bom.pdf"),
    ],
)
def test_safe_filename_strips_path_components(raw: str, expected: str) -> None:
    assert upload._safe_filename(raw) == expected


def test_safe_filename_pure_traversal_is_empty() -> None:
    # A name that is only separators / dots collapses to "" → caller rejects it.
    assert upload._safe_filename("../../") == ""
