"""Tests for the ingestion pipeline — tests every file in data/input/PDF_POC."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.models import FileFormat, ParsedBOM
from src.ingestion.file_router import detect_format, infer_customer
from src.ingestion.structure_normalizer import parse_file_sync as parse_file

# Root of test data
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "input" / "PDF_POC"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def all_customer_bom_files() -> list[tuple[str, Path]]:
    """Collect all customer BOM files (exclude Schaufler templates)."""
    files = []
    for f in sorted(DATA_DIR.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".pdf", ".xlsx", ".xls"):
            continue
        # Skip Schaufler target templates
        if "CadCam_Stuecklistenvorlage" in f.name:
            continue
        customer = infer_customer(f)
        files.append((customer, f))
    return files


def all_template_files() -> list[Path]:
    """Collect all Schaufler CadCam template files."""
    return sorted(
        f for f in DATA_DIR.rglob("CadCam_Stuecklistenvorlage*") if f.is_file()
    )


# ---------------------------------------------------------------------------
# Parametrize: one test per customer BOM file
# ---------------------------------------------------------------------------

CUSTOMER_FILES = all_customer_bom_files()
TEMPLATE_FILES = all_template_files()


@pytest.fixture(params=CUSTOMER_FILES, ids=[f"{c}/{p.name}" for c, p in CUSTOMER_FILES])
def customer_bom(request) -> tuple[str, Path]:
    return request.param


@pytest.fixture(params=TEMPLATE_FILES, ids=[f.name for f in TEMPLATE_FILES])
def template_file(request) -> Path:
    return request.param


# ---------------------------------------------------------------------------
# Test: File Router
# ---------------------------------------------------------------------------


class TestFileRouter:
    def test_pdf_detection(self):
        pdf_files = [f for _, f in CUSTOMER_FILES if f.suffix.lower() == ".pdf"]
        assert len(pdf_files) > 0
        for f in pdf_files:
            assert detect_format(f) == FileFormat.PDF

    def test_excel_detection(self):
        xlsx_files = [f for _, f in CUSTOMER_FILES if f.suffix.lower() == ".xlsx"]
        for f in xlsx_files:
            assert detect_format(f) == FileFormat.EXCEL

    def test_template_detection(self):
        for f in TEMPLATE_FILES[:3]:
            assert detect_format(f) == FileFormat.EXCEL

    def test_customer_inference(self):
        for customer, f in CUSTOMER_FILES:
            inferred = infer_customer(f)
            assert inferred, f"No customer inferred for {f}"


# ---------------------------------------------------------------------------
# Test: Parsing every customer file
# ---------------------------------------------------------------------------


class TestParseCustomerBOMs:
    """Parse every customer BOM and verify basic quality criteria."""

    def test_parse_returns_result(self, customer_bom: tuple[str, Path]):
        customer, filepath = customer_bom
        result = parse_file(filepath)
        assert isinstance(result, ParsedBOM)
        assert result.source.filename == filepath.name

    def test_has_headers(self, customer_bom: tuple[str, Path]):
        customer, filepath = customer_bom
        result = parse_file(filepath)
        assert (
            len(result.headers) >= 2
        ), f"{customer}/{filepath.name}: Only {len(result.headers)} headers found"

    def test_has_rows(self, customer_bom: tuple[str, Path]):
        customer, filepath = customer_bom
        result = parse_file(filepath)
        assert (
            result.total_rows >= 1
        ), f"{customer}/{filepath.name}: No data rows extracted"

    def test_rows_have_values(self, customer_bom: tuple[str, Path]):
        customer, filepath = customer_bom
        result = parse_file(filepath)
        if result.total_rows == 0:
            pytest.skip("No rows extracted")
        # At least some rows should have non-None values
        non_empty = sum(
            1 for row in result.rows if any(v is not None for v in row.values())
        )
        assert non_empty >= 1, f"{customer}/{filepath.name}: All rows are empty"

    def test_confidence_above_zero(self, customer_bom: tuple[str, Path]):
        customer, filepath = customer_bom
        result = parse_file(filepath)
        assert (
            result.source.extraction_confidence > 0
        ), f"{customer}/{filepath.name}: Zero confidence"


# ---------------------------------------------------------------------------
# Test: Parsing Schaufler templates
# ---------------------------------------------------------------------------


class TestParseTemplates:
    def test_parse_template(self, template_file: Path):
        result = parse_file(template_file)
        assert isinstance(result, ParsedBOM)
        assert result.source.format == FileFormat.EXCEL
        # Templates should have the Stückliste columns
        assert result.total_columns >= 10

    def test_template_has_data(self, template_file: Path):
        result = parse_file(template_file)
        # Most templates have data filled in
        assert result.total_rows >= 1


# ---------------------------------------------------------------------------
# Test: Specific known formats
# ---------------------------------------------------------------------------


class TestSpecificFormats:
    """Test that specific, known BOM formats are parsed correctly."""

    def _find_file(self, partial_name: str) -> Path | None:
        for _, f in CUSTOMER_FILES:
            if partial_name in f.name:
                return f
        return None

    def test_audi_ulysses_format(self):
        f = self._find_file("K4594")
        if f is None:
            pytest.skip("Audi file not found")
        result = parse_file(f)
        # Audi Ulysses should have ~16 columns
        assert result.total_columns >= 5, f"Only {result.total_columns} columns"
        assert result.total_rows >= 50, f"Only {result.total_rows} rows"

    def test_fca_stocklist(self):
        f = self._find_file("20283")
        if f is None:
            pytest.skip("FCA file not found")
        result = parse_file(f)
        assert result.total_columns >= 4
        assert result.total_rows >= 50

    def test_ford_tool_kit(self):
        f = self._find_file("N4329")
        if f is None:
            pytest.skip("Ford file not found")
        result = parse_file(f)
        assert result.total_columns >= 5
        assert result.total_rows >= 10

    def test_gf_stueckliste(self):
        f = self._find_file("STL_08")
        if f is None:
            pytest.skip("GF file not found")
        result = parse_file(f)
        # GF has fragmented tables — at least some data should be extracted
        assert result.total_rows >= 5, f"GF: only {result.total_rows} rows"

    def test_ljunghaell_czech(self):
        f = self._find_file("F1896")
        if f is None:
            pytest.skip("Ljunghaell file not found")
        result = parse_file(f)
        assert result.total_columns >= 5
        assert result.total_rows >= 20

    def test_magna_ulysses(self):
        f = self._find_file("K4623")
        if f is None:
            pytest.skip("Magna file not found")
        result = parse_file(f)
        assert result.total_columns >= 5
        assert result.total_rows >= 50

    def test_mercedes_av_stueckliste(self):
        f = self._find_file("254_DE20")
        if f is None:
            pytest.skip("Mercedes AV file not found")
        result = parse_file(f)
        assert result.total_columns >= 5
        assert result.total_rows >= 50

    def test_mercedes_excel_8413(self):
        f = self._find_file("8413.xlsx")
        if f is None:
            pytest.skip("Mercedes 8413.xlsx not found")
        result = parse_file(f)
        assert result.source.format == FileFormat.EXCEL
        assert result.total_columns >= 20
        assert result.total_rows >= 100

    def test_scania_material_list(self):
        f = self._find_file("127-2158")
        if f is None:
            pytest.skip("Scania file not found")
        result = parse_file(f)
        assert result.total_columns >= 10
        assert result.total_rows >= 20

    def test_tcg_stueckliste(self):
        f = self._find_file("Stueckliste_900820")
        if f is None:
            pytest.skip("TCG file not found")
        result = parse_file(f)
        # TCG has CAD frame issues — should still extract some data
        assert result.total_rows >= 5, f"TCG: only {result.total_rows} rows"

    def test_tcg_stckliste_ohne_ver(self):
        f = self._find_file("stckliste_ohne_ver")
        if f is None:
            pytest.skip("TCG ohne ver file not found")
        result = parse_file(f)
        assert result.total_rows >= 5, f"TCG ohne ver: only {result.total_rows} rows"

    def test_zf_stueckliste(self):
        f = self._find_file("f156900400")
        if f is None:
            pytest.skip("ZF file not found")
        result = parse_file(f)
        assert result.total_columns >= 5
        assert result.total_rows >= 50

    def test_linamar_tdda(self):
        f = self._find_file("8185-13")
        if f is None:
            pytest.skip("Linamar file not found")
        result = parse_file(f)
        # Linamar is only 1 page with assembly-level data
        assert result.total_rows >= 3
