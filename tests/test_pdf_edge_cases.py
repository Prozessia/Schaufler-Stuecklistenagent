from __future__ import annotations

from pathlib import Path

import pytest

from src.core.models import SourceLocation
from src.core.models import ExtractionMethod, FileFormat, ParsedBOM, SourceMetadata
from src.ingestion import pdf_common, structure_normalizer
from src.ingestion.pdf_common import PasswordProtectedPdfError
import src.ingestion.coordinate_table as coordinate_table
import src.ingestion.pdf_parser as pdf_parser
import src.ingestion.pdf_parser_legacy as pdf_parser_legacy


class _EncryptedDoc:
    def __init__(self) -> None:
        self.needs_pass = True
        self.is_encrypted = False
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_open_pdf_document_rejects_password_protected_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "secret.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 secret")
    encrypted_doc = _EncryptedDoc()

    monkeypatch.setattr(pdf_common.fitz, "open", lambda _path: encrypted_doc)

    with pytest.raises(PasswordProtectedPdfError, match="password-protected"):
        pdf_common.open_pdf_document(pdf_path)

    assert encrypted_doc.closed is True


class _FakePage:
    """Minimal stand-in for fitz.Page with rect.height + get_text('blocks')."""

    def __init__(self, blocks: list[tuple], height: float) -> None:
        self.rect = type("Rect", (), {"height": height})()
        self._blocks = blocks

    def get_text(self, _kind: str, sort: bool = False) -> list[tuple]:
        return self._blocks


def test_header_footer_geometrically_excluded() -> None:
    """C1: blocks in the top/bottom 8% margin are dropped, only the middle survives."""
    # (x0, y0, x1, y1, text) — centers at y=10 (header), 200 (mid), 790 (footer)
    blocks = [
        (0.0, 5.0, 100.0, 15.0, "Seite 1 von 3"),   # center 10  → header band
        (0.0, 195.0, 100.0, 205.0, "1 Formplatte"),  # center 200 → body
        (0.0, 785.0, 100.0, 795.0, "Rev. 3"),        # center 790 → footer band
    ]
    page = _FakePage(blocks, height=800.0)

    result = pdf_parser._extract_text_blocks(page)

    assert len(result) == 1
    assert result[0]["text"] == "1 Formplatte"


def test_dedup_preserves_distinct_rows() -> None:
    """A1: rows sharing the first 3 columns but differing later must NOT be dropped.

    Regression guard for the silent data-loss vector where _deduplicate_rows
    keyed only on the first 3 columns.
    """
    columns = ["POS", "BENENNUNG", "STK", "WERKST", "FERTIGMASS"]

    rows: list[dict[str, str | None]] = [
        # 3 rows share POS/BENENNUNG/STK but differ in WERKST/FERTIGMASS
        {"POS": "1", "BENENNUNG": "Formplatte", "STK": "2",
         "WERKST": "1.2343", "FERTIGMASS": "100 x 50 x 20"},
        {"POS": "1", "BENENNUNG": "Formplatte", "STK": "2",
         "WERKST": "1.2344", "FERTIGMASS": "200 x 80 x 25"},
        {"POS": "1", "BENENNUNG": "Formplatte", "STK": "2",
         "WERKST": "AlSi9Cu3", "FERTIGMASS": "300 x 90 x 30"},
        # 7 further distinct rows
        {"POS": "2", "BENENNUNG": "Schieber", "STK": "1",
         "WERKST": "1.2343", "FERTIGMASS": "120 x 60 x 15"},
        {"POS": "3", "BENENNUNG": "Kern", "STK": "4",
         "WERKST": "1.2343", "FERTIGMASS": "Ø 40 x 80"},
        {"POS": "4", "BENENNUNG": "Einsatz", "STK": "1",
         "WERKST": "1.2344", "FERTIGMASS": "50 x 50 x 50"},
        {"POS": "5", "BENENNUNG": "Auswerfer", "STK": "8",
         "WERKST": "1.2210", "FERTIGMASS": "Ø 8 x 120"},
        {"POS": "6", "BENENNUNG": "Platte", "STK": "1",
         "WERKST": "1.1730", "FERTIGMASS": "400 x 300 x 40"},
        {"POS": "7", "BENENNUNG": "Buchse", "STK": "2",
         "WERKST": "CuBe2", "FERTIGMASS": "Ø 20 x 35"},
        {"POS": "8", "BENENNUNG": "Düse", "STK": "1",
         "WERKST": "1.2343", "FERTIGMASS": "Ø 12 x 60"},
    ]

    result = pdf_parser._deduplicate_rows(rows, columns)

    assert len(result) == 10, "all distinct rows must survive deduplication"
    assert result == rows


def test_dedup_removes_only_exact_full_duplicates() -> None:
    """A1: an exact full-row duplicate (e.g. repeated header) is still removed."""
    columns = ["POS", "BENENNUNG", "STK"]
    rows: list[dict[str, str | None]] = [
        {"POS": "1", "BENENNUNG": "Formplatte", "STK": "2"},
        {"POS": "1", "BENENNUNG": "Formplatte", "STK": "2"},  # exact dup
        {"POS": "2", "BENENNUNG": "Schieber", "STK": "1"},
    ]

    result = pdf_parser._deduplicate_rows(rows, columns)

    assert len(result) == 2
    assert result == [rows[0], rows[2]]


@pytest.mark.asyncio
async def test_parse_file_skips_legacy_fallback_for_password_protected_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "secret.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 secret")

    async def _raise_password_error(*_args: object, **_kwargs: object) -> object:
        raise PasswordProtectedPdfError("PDF is password-protected: secret.pdf")

    def _legacy_parse(_filepath: Path) -> object:
        raise AssertionError("legacy fallback must not run for password-protected PDFs")

    monkeypatch.setattr(pdf_parser, "parse_pdf", _raise_password_error)
    monkeypatch.setattr(pdf_parser_legacy, "parse_pdf", _legacy_parse)

    with pytest.raises(PasswordProtectedPdfError, match="password-protected"):
        await structure_normalizer.parse_file(pdf_path, llm=object())


@pytest.mark.asyncio
async def test_parse_file_uses_deterministic_reconstruction_for_text_based_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RB-1 (3.5): text-layer PDFs route to the deterministic reconstructor."""
    pdf_path = tmp_path / "text-layer.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 text-layer")

    expected = ParsedBOM(
        source=SourceMetadata(
            filename=pdf_path.name,
            filepath=str(pdf_path),
            customer="ACME",
            format=FileFormat.PDF,
            extraction_method=ExtractionMethod.PYMUPDF_TEXT,
            extraction_confidence=0.97,
        ),
        headers=["POS", "BENENNUNG", "STK"],
        rows=[{"POS": "1", "BENENNUNG": "Einsatz", "STK": "1"}],
        row_keys=["p0:b0000"],
        pdf_row_bands=["p0:b0000"],
        metadata={"has_text_layer": True},
    )

    monkeypatch.setattr(pdf_parser, "pdf_has_text_layer", lambda _filepath: True)

    async def _reconstruct(_filepath: Path, _llm: object) -> ParsedBOM:
        return expected

    async def _parse_vision(_filepath: Path, _llm: object) -> ParsedBOM:
        raise AssertionError("vision parser must not run for healthy text-layer PDFs")

    def _legacy_parse(_filepath: Path) -> ParsedBOM:
        raise AssertionError("legacy parser must not run for healthy text-layer PDFs")

    monkeypatch.setattr(coordinate_table, "reconstruct_table", _reconstruct)
    monkeypatch.setattr(pdf_parser, "parse_pdf", _parse_vision)
    monkeypatch.setattr(pdf_parser_legacy, "parse_pdf", _legacy_parse)

    result = await structure_normalizer.parse_file(pdf_path, llm=object())

    assert result.source.extraction_method == ExtractionMethod.PYMUPDF_TEXT
    assert result.row_keys == ["p0:b0000"]
    assert result.headers == ["POS", "BENENNUNG", "STK"]


@pytest.mark.asyncio
async def test_parse_file_falls_back_to_vision_when_reconstruction_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RB-1 (3.5): a declined reconstruction (e.g. GF) falls back to Vision."""
    pdf_path = tmp_path / "rotated.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 rotated")

    vision_result = ParsedBOM(
        source=SourceMetadata(
            filename=pdf_path.name,
            filepath=str(pdf_path),
            customer="ACME",
            format=FileFormat.PDF,
            extraction_method=ExtractionMethod.GPT4O_VISION,
            extraction_confidence=0.92,
        ),
        headers=["POS", "BENENNUNG", "STK"],
        rows=[{"POS": "1", "BENENNUNG": "Einsatz", "STK": "1"}],
        metadata={"has_text_layer": False},
    )

    monkeypatch.setattr(pdf_parser, "pdf_has_text_layer", lambda _filepath: True)

    async def _decline(_filepath: Path, _llm: object) -> ParsedBOM:
        from src.ingestion.pdf_common import ExtractionError

        raise ExtractionError("no reliable BOM table structure")

    async def _parse_vision(_filepath: Path, _llm: object) -> ParsedBOM:
        return vision_result

    monkeypatch.setattr(coordinate_table, "reconstruct_table", _decline)
    monkeypatch.setattr(pdf_parser, "parse_pdf", _parse_vision)

    result = await structure_normalizer.parse_file(pdf_path, llm=object())

    assert result.source.extraction_method == ExtractionMethod.GPT4O_VISION


def test_source_location_drops_invalid_bbox() -> None:
    location = SourceLocation(
        page=1,
        bbox=[20, 10, 5, 30],
        text="Demo",
        match_type="column_corridor",
    )

    assert location.bbox is None


def test_strip_llm_json_payload_removes_code_fences() -> None:
    strip_payload = getattr(pdf_parser, "_strip_llm_json_payload")
    content = '\n```json\n  {\n    "columns": ["POS"],\n    "rows": []\n  }\n```\n'

    cleaned = strip_payload(content)

    assert cleaned == '{\n    "columns": ["POS"],\n    "rows": []\n  }'


def test_extract_pdf_page_texts_preserves_block_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extract_page_texts = getattr(pdf_parser, "_extract_pdf_page_texts")
    pdf_path = tmp_path / "layout.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 layout")

    class _FakePage:
        def get_text(self, mode: str, sort: bool = False):
            assert sort is True or mode != "blocks"
            if mode == "blocks":
                return [
                    (10.0, 10.0, 40.0, 22.0, "POS", 0, 0),
                    (
                        80.0,
                        10.0,
                        220.0,
                        28.0,
                        "Material description\nsecond line",
                        0,
                        1,
                    ),
                    (260.0, 10.0, 290.0, 22.0, "2", 0, 2),
                    (10.0, 40.0, 40.0, 52.0, "20", 0, 3),
                    (80.0, 40.0, 220.0, 52.0, "Core plate", 0, 4),
                    (260.0, 40.0, 290.0, 52.0, "1", 0, 5),
                ]
            if mode == "text":
                return "fallback text"
            raise AssertionError(f"unexpected text mode: {mode}")

    class _FakeDoc(list):
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        pdf_parser, "open_pdf_document", lambda _filepath: _FakeDoc([_FakePage()])
    )

    texts = extract_page_texts(pdf_path)

    assert len(texts) == 1
    assert (
        "ROW 001: [x=10-40] POS || [x=80-220] Material description / second line || [x=260-290] 2"
        in texts[0]
    )
    assert "ROW 002: [x=10-40] 20 || [x=80-220] Core plate || [x=260-290] 1" in texts[0]

