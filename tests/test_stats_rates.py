"""FE-003 — automation_rate uses total_scored (green+yellow+red+manual_confirmed),
NEUTRAL excluded from denominator."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build minimal Job-like objects without touching any DB
# ---------------------------------------------------------------------------


@dataclass
class _FakeJob:
    job_id: str = "j1"
    filename: str = "bom.pdf"
    filepath: Path = Path("/tmp/bom.pdf")
    customer: str = ""
    status: str = "completed"
    progress: float = 1.0
    error: str | None = None
    audit: None = None
    export_path: None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_rows: int = 10
    total_cells: int = 20
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    neutral_count: int = 0
    manual_confirmed_count: int = 0
    completeness_guaranteed: bool = False
    expected_position_count: int = 0
    archived: bool = False


def _make_jobs(
    *,
    green: int,
    yellow: int,
    red: int,
    neutral: int,
    manual_confirmed: int = 0,
) -> list[_FakeJob]:
    j = _FakeJob()
    j.green_count = green
    j.yellow_count = yellow
    j.red_count = red
    j.neutral_count = neutral
    j.manual_confirmed_count = manual_confirmed
    j.total_cells = green + yellow + red + neutral + manual_confirmed
    return [j]


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------


def test_automation_rate_excludes_neutral() -> None:
    """green=8, yellow=1, red=1, neutral=10 → rate = 8/10 = 0.8 (not 8/20)."""
    jobs = _make_jobs(green=8, yellow=1, red=1, neutral=10)

    # Patch list_summaries so overview() sees our fake jobs without a DB.
    with (
        patch("src.api.routes.stats.job_store") as mock_store,
        patch("src.api.routes.stats._feedback") as mock_fb,
    ):
        mock_store.list_summaries.return_value = jobs
        mock_fb.correction_count.return_value = 0

        from src.api.routes.stats import overview

        result = overview()

    assert result["green"] == 8
    assert result["yellow"] == 1
    assert result["red"] == 1
    assert result["neutral"] == 10
    assert result["total_scored"] == 10  # 8+1+1+0, not 20
    assert result["automation_rate"] == pytest.approx(0.8)


def test_automation_rate_zero_when_nothing_scored() -> None:
    """No scored cells → rate=0.0 (no ZeroDivisionError)."""
    jobs = _make_jobs(green=0, yellow=0, red=0, neutral=5)

    with (
        patch("src.api.routes.stats.job_store") as mock_store,
        patch("src.api.routes.stats._feedback") as mock_fb,
    ):
        mock_store.list_summaries.return_value = jobs
        mock_fb.correction_count.return_value = 0

        from src.api.routes.stats import overview

        result = overview()

    assert result["automation_rate"] == 0.0
    assert result["total_scored"] == 0


def test_minutes_per_row_from_config() -> None:
    """_minutes_per_row() returns value from app_config when present."""
    fake_config = {"stats": {"minutes_per_row": 5.0}}
    with patch("src.api.routes.stats.load_app_config", return_value=fake_config):
        from src.api.routes.stats import _minutes_per_row

        assert _minutes_per_row() == pytest.approx(5.0)


def test_minutes_per_row_default() -> None:
    """_minutes_per_row() falls back to 3.0 when stats key missing."""
    fake_config: dict = {}
    with patch("src.api.routes.stats.load_app_config", return_value=fake_config):
        from src.api.routes.stats import _minutes_per_row

        assert _minutes_per_row() == pytest.approx(3.0)
