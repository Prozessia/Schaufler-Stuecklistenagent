"""Feedback Store — persist user corrections for learning over time.

Stores corrections as JSONL (one JSON object per line) so they can
be used as few-shot examples for future mapping/transformation of
the same customer or similar BOM formats.

File: data/learned_mappings/corrections.jsonl
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CORRECTIONS_PATH = _PROJECT_ROOT / "data" / "learned_mappings" / "corrections.jsonl"


class Correction(BaseModel):
    """A single user correction record."""

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    customer: str = ""
    source_file: str = ""
    row_index: int = 0
    target_field: str = ""
    target_column: str = ""
    raw_value: str | None = None
    original_transformed: str | None = None
    corrected_value: str = ""
    original_score: float = 0.0
    original_classification: str = ""
    correction_type: str = ""  # "value", "mapping", "rejected"


class FeedbackStore:
    """Append-only store for user corrections."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_CORRECTIONS_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def add_correction(self, correction: Correction) -> None:
        """Append a single correction to the JSONL file."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(correction.model_dump_json() + "\n")
        logger.info(
            "Correction saved: %s / %s / row %d / %s",
            correction.customer,
            correction.target_field,
            correction.row_index,
            correction.correction_type,
        )

    def add_corrections(self, corrections: list[Correction]) -> None:
        """Append multiple corrections at once."""
        with self._path.open("a", encoding="utf-8") as f:
            for c in corrections:
                f.write(c.model_dump_json() + "\n")
        logger.info("Saved %d corrections", len(corrections))

    def load_corrections(self, customer: str | None = None) -> list[Correction]:
        """Load all corrections, optionally filtered by customer."""
        if not self._path.exists():
            return []
        corrections: list[Correction] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = Correction.model_validate_json(line)
                    if customer is None or c.customer.lower() == customer.lower():
                        corrections.append(c)
                except Exception as e:
                    logger.warning("Skipping malformed correction line: %s", e)
        return corrections

    def get_field_corrections(
        self, customer: str, target_field: str
    ) -> list[Correction]:
        """Get corrections for a specific customer and field."""
        return [
            c
            for c in self.load_corrections(customer)
            if c.target_field == target_field
        ]

    def correction_count(self) -> int:
        """Count total corrections stored."""
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def stats(self) -> dict[str, int]:
        """Return correction stats grouped by customer."""
        all_corrections = self.load_corrections()
        by_customer: dict[str, int] = {}
        for c in all_corrections:
            by_customer[c.customer] = by_customer.get(c.customer, 0) + 1
        return by_customer
