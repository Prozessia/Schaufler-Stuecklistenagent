from __future__ import annotations

from pathlib import Path

import pytest

from src.api import pipeline_runner
from src.api.job_store import JobStore
from src.core.models import ExtractionMethod, FileFormat, ParsedBOM, SourceMetadata
from src.mapping.llm_column_mapper import MappingResult
import src.llm.azure_openai as azure_openai_module


class _StubLLM:
    pass


class _StubCounterCheckService:
    def __init__(self, llm: object) -> None:
        self.llm = llm
        self.released_job_id: str | None = None
        self.closed = False

    def release_job(self, job_id: str) -> None:
        self.released_job_id = job_id

    def close(self) -> None:
        self.closed = True


class _FakeLLMTimeout(Exception):
    pass


@pytest.mark.asyncio
async def test_pipeline_marks_job_failed_when_mapping_raises_after_30_percent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path=db_path)
    source_file = tmp_path / "demo.pdf"
    source_file.write_bytes(b"%PDF-1.4 demo")
    store.create("job-30-stuck", source_file.name, source_file)

    async def _parse_file(filepath: Path, llm: object | None = None) -> ParsedBOM:
        assert filepath == source_file
        assert llm is not None
        return ParsedBOM(
            source=SourceMetadata(
                filename=source_file.name,
                filepath=str(source_file),
                customer="Demo Customer",
                format=FileFormat.PDF,
                extraction_method=ExtractionMethod.PYMUPDF_TABLE,
                extraction_confidence=0.9,
            ),
            headers=["Pos"],
            rows=[{"Pos": "1"}],
        )

    async def _map_columns(*_args: object, **_kwargs: object) -> object:
        raise _FakeLLMTimeout("Azure OpenAI request timed out")

    monkeypatch.setattr(pipeline_runner, "job_store", store)
    monkeypatch.setattr(azure_openai_module, "AzureOpenAILLM", _StubLLM)
    monkeypatch.setattr(
        pipeline_runner, "VisionCounterCheckService", _StubCounterCheckService
    )
    monkeypatch.setattr(pipeline_runner, "parse_file", _parse_file)
    monkeypatch.setattr(pipeline_runner, "load_schema", lambda: object())
    monkeypatch.setattr(pipeline_runner, "map_columns", _map_columns)

    await pipeline_runner.run_pipeline("job-30-stuck")

    job = store.get("job-30-stuck")
    assert job is not None
    assert job.status == "failed"
    assert job.progress == pytest.approx(0.3)
    assert job.error is not None
    assert "mapping columns failed" in job.error.lower()
    assert "timed out" in job.error.lower()

    store.close()


@pytest.mark.asyncio
async def test_pipeline_surfaces_mapping_json_parse_error_in_job_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path=db_path)
    source_file = tmp_path / "demo.pdf"
    source_file.write_bytes(b"%PDF-1.4 demo")
    store.create("job-json-error", source_file.name, source_file)

    async def _parse_file(filepath: Path, llm: object | None = None) -> ParsedBOM:
        assert filepath == source_file
        assert llm is not None
        return ParsedBOM(
            source=SourceMetadata(
                filename=source_file.name,
                filepath=str(source_file),
                customer="Demo Customer",
                format=FileFormat.PDF,
                extraction_method=ExtractionMethod.PYMUPDF_TABLE,
                extraction_confidence=0.9,
            ),
            headers=["Pos"],
            rows=[{"Pos": "1"}],
        )

    async def _map_columns(*_args: object, **_kwargs: object) -> MappingResult:
        return MappingResult(
            source_file=source_file.name,
            customer="Demo Customer",
            mappings=[],
            notes="JSON parse error: Expecting value at line 1 column 1",
        )

    monkeypatch.setattr(pipeline_runner, "job_store", store)
    monkeypatch.setattr(azure_openai_module, "AzureOpenAILLM", _StubLLM)
    monkeypatch.setattr(
        pipeline_runner, "VisionCounterCheckService", _StubCounterCheckService
    )
    monkeypatch.setattr(pipeline_runner, "parse_file", _parse_file)
    monkeypatch.setattr(pipeline_runner, "load_schema", lambda: object())
    monkeypatch.setattr(pipeline_runner, "map_columns", _map_columns)

    await pipeline_runner.run_pipeline("job-json-error")

    job = store.get("job-json-error")
    assert job is not None
    assert job.status == "failed"
    assert job.error == "JSON parse error: Expecting value at line 1 column 1"

    store.close()
