"""ARCH-002 (Yellow-Recheck für Scans) + PERF-002 (Batch-Counter-Check).

Scan-path cells can only ever reach GREEN through the verified-scan gate, which
requires a PASSED counter-check — these tests cover the scorer-level candidacy
(previously the pre-gate blocked scans before the counter-check could run, so
scan-GREEN was structurally dead) and the page-batched verification (one Vision
call per page instead of one per cell).
"""

from __future__ import annotations

from pathlib import Path

from src.core.models import ExtractionMethod, FileFormat, ParsedBOM, SourceMetadata
from src.mapping.llm_column_mapper import ColumnMapping, MappingResult
from src.mapping.schema_registry import TargetField, TargetSchema
from src.scoring.ensemble_scorer import score_bom
from src.scoring.threshold_manager import ScoringConfig, TrafficLight
from src.scoring.vision_verifier import CounterCheckResult
from src.transform.pipeline import transform_bom


class _BatchStub:
    """Scripted batch counter-check; counts CALLS (not cells)."""

    def __init__(self, *, passed: bool) -> None:
        self._passed = passed
        self.calls = 0
        self.batch_sizes: list[int] = []

    async def verify_fields(self, job_id, pdf_path, page_number, requests):
        self.calls += 1
        self.batch_sizes.append(len(requests))
        return {
            r.request_id: CounterCheckResult(
                passed=self._passed,
                score=1.0 if self._passed else 0.0,
                reason="stub",
                notes=f"stub_batch primary={r.primary_value}",
                secondary_value=r.primary_value,
                secondary_confidence=0.99,
            )
            for r in requests
        }


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            TargetField(
                name="Design Count",
                name_de="Stückzahl",
                column="D",
                type="integer",
                required=True,
            ),
            TargetField(
                name="Description",
                name_de="Benennung",
                column="F",
                type="string",
                required=True,
            ),
        ]
    )


def _mapping() -> MappingResult:
    return MappingResult(
        source_file="scan",
        customer="ACME",
        mappings=[
            ColumnMapping(
                source_column="Qty",
                target_field="Design Count",
                target_column="D",
                confidence=0.97,
                reasoning="qty",
                candidate_confidence=0.97,
                candidate_reasoning="qty",
            ),
            ColumnMapping(
                source_column="Name",
                target_field="Description",
                target_column="F",
                confidence=0.97,
                reasoning="name",
                candidate_confidence=0.97,
                candidate_reasoning="name",
            ),
        ],
    )


def _scan_bom(qty: str = "4", name: str = "ANGLE PLATE") -> ParsedBOM:
    """A Vision-extracted scan: no text layer, but per-cell page hints."""
    return ParsedBOM(
        source=SourceMetadata(
            filename="scan.pdf",
            filepath="scan.pdf",
            customer="ACME",
            format=FileFormat.PDF,
            extraction_method=ExtractionMethod.GPT4O_VISION,
            extraction_confidence=0.92,
        ),
        headers=["Qty", "Name"],
        rows=[{"Qty": qty, "Name": name}],
        metadata={
            "has_text_layer": False,
            "source_locations": {
                0: {
                    "Qty": {
                        "page": 1,
                        "bbox": [10.0, 20.0, 30.0, 40.0],
                        "text": qty,
                        "match_type": "column_corridor",
                    },
                    "Name": {
                        "page": 1,
                        "bbox": [50.0, 20.0, 90.0, 40.0],
                        "text": name,
                        "match_type": "column_corridor",
                    },
                }
            },
        },
    )


def _recheck_config(**overrides) -> ScoringConfig:
    defaults = dict(
        enable_counter_check=True,
        enable_yellow_recheck=True,
        verify_green_threshold=0.60,
    )
    defaults.update(overrides)
    return ScoringConfig(**defaults)


def _score_scan(stub, config) -> object:
    bom = _scan_bom()
    schema = _schema()
    mapping = _mapping()
    transformed = transform_bom(bom, mapping, schema=schema)
    return score_bom(
        transformed,
        mapping,
        schema=schema,
        config=config,
        counter_check_service=stub,
        job_id="job-scan",
        pdf_path=Path("scan.pdf"),
    )


def test_scan_cells_promoted_via_batched_counter_check() -> None:
    """Verified-scan GREEN works end-to-end — and both cells share ONE call."""
    stub = _BatchStub(passed=True)
    audit = _score_scan(stub, _recheck_config())

    assert stub.calls == 1, "two candidate cells on one page must batch into one call"
    assert stub.batch_sizes == [2]
    by_field = {c.target_field: c for c in audit.cells}
    assert by_field["Design Count"].classification == TrafficLight.GREEN
    assert by_field["Description"].classification == TrafficLight.GREEN
    assert "SCAN_VERIFIED_WITHOUT_TEXT_LAYER" in by_field["Design Count"].green_evidence
    assert audit.green_count == 2
    assert audit.yellow_count == 0


def test_scan_cells_stay_yellow_when_counter_check_fails() -> None:
    stub = _BatchStub(passed=False)
    audit = _score_scan(stub, _recheck_config())

    assert stub.calls == 1
    for cell in audit.cells:
        assert cell.classification == TrafficLight.YELLOW
        assert "PROMOTED_BY_BATCH_COUNTER_CHECK" not in cell.reasoning
    assert audit.green_count == 0


def test_scan_recheck_disabled_means_no_calls_and_no_green() -> None:
    """Without enable_yellow_recheck the scan path stays counter-check-free."""
    stub = _BatchStub(passed=True)
    audit = _score_scan(stub, _recheck_config(enable_yellow_recheck=False))

    assert stub.calls == 0
    assert audit.green_count == 0


def test_scan_recheck_respects_counter_check_master_switch() -> None:
    stub = _BatchStub(passed=True)
    audit = _score_scan(
        stub, _recheck_config(enable_counter_check=False)
    )

    assert stub.calls == 0
    assert audit.green_count == 0


def test_batch_error_keeps_cells_yellow() -> None:
    class _Broken:
        async def verify_fields(self, *a, **kw):
            raise RuntimeError("azure down")

    audit = _score_scan(_Broken(), _recheck_config())
    for cell in audit.cells:
        assert cell.classification == TrafficLight.YELLOW
        assert "counter_check_error" in cell.counter_check_notes
    assert audit.green_count == 0
