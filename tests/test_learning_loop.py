"""Sprint 3 — item 13: persisted corrections are fed back into the mapping prompt."""

from __future__ import annotations

from pathlib import Path

from src.core.models import FileFormat, ParsedBOM, SourceMetadata
from src.export.feedback_store import Correction, FeedbackStore
from src.mapping.llm_column_mapper import build_mapping_prompt
from src.mapping.schema_registry import load_schema


def _bom(customer: str) -> ParsedBOM:
    return ParsedBOM(
        source=SourceMetadata(
            filename="x.pdf",
            filepath="x.pdf",
            customer=customer,
            format=FileFormat.PDF,
        ),
        headers=["Pos", "Bezeichnung"],
        rows=[{"Pos": "1", "Bezeichnung": "Formplatte"}],
    )


def _store_with_correction(tmp_path: Path, customer: str) -> FeedbackStore:
    store = FeedbackStore(path=tmp_path / "corrections.jsonl")
    store.add_correction(
        Correction(
            customer=customer,
            target_field="Benennung",
            raw_value="Plate",
            corrected_value="Formplatte",
            correction_type="value",
        )
    )
    return store


def test_corrections_injected_for_known_customer(tmp_path: Path) -> None:
    store = _store_with_correction(tmp_path, "ACME")
    system, _ = build_mapping_prompt(_bom("ACME"), load_schema(), feedback_store=store)

    assert "LEARNED CORRECTIONS" in system
    assert "Formplatte" in system
    assert 'field "Benennung"' in system


def test_no_corrections_for_unknown_customer(tmp_path: Path) -> None:
    store = _store_with_correction(tmp_path, "ACME")
    system, _ = build_mapping_prompt(_bom("OtherCo"), load_schema(), feedback_store=store)

    assert "LEARNED CORRECTIONS" not in system


def test_prompt_unchanged_when_store_empty(tmp_path: Path) -> None:
    store = FeedbackStore(path=tmp_path / "empty.jsonl")
    system, _ = build_mapping_prompt(_bom("ACME"), load_schema(), feedback_store=store)

    assert "LEARNED CORRECTIONS" not in system
