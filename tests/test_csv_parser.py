"""Unit tests for parse_csv (BUG-018)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.models import ExtractionMethod, FileFormat
from src.ingestion.excel_parser import parse_csv


def _write(tmp_path: Path, name: str, content: str, encoding: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes(content.encode(encoding))
    return p


def test_semicolon_csv_parses_correctly(tmp_path: Path) -> None:
    """German semicolon-delimited CSV with header in row 1."""
    content = "Pos;Benennung;Stk;Werkstoff\n1;Formplatte;2;1.2343\n2;Schieber;1;1.2344\n3;Kern;4;1.2343\n"
    path = _write(tmp_path, "test_semi.csv", content)

    result = parse_csv(path)

    assert result.source.format == FileFormat.CSV
    assert result.source.extraction_method == ExtractionMethod.CSV
    assert result.source.extraction_confidence == 0.95
    assert result.headers == ["Pos", "Benennung", "Stk", "Werkstoff"]
    assert len(result.rows) == 3
    assert result.rows[0]["Pos"] == "1"
    assert result.rows[0]["Benennung"] == "Formplatte"
    assert result.metadata["delimiter"] == ";"


def test_comma_csv_parses_correctly(tmp_path: Path) -> None:
    """Standard comma-delimited CSV."""
    content = "Pos,Benennung,Stk,Werkstoff\n1,Formplatte,2,1.2343\n2,Schieber,1,1.2344\n3,Kern,4,1.2343\n"
    path = _write(tmp_path, "test_comma.csv", content)

    result = parse_csv(path)

    assert result.headers == ["Pos", "Benennung", "Stk", "Werkstoff"]
    assert len(result.rows) == 3
    assert result.rows[1]["Benennung"] == "Schieber"
    assert result.metadata["delimiter"] == ","


def test_empty_csv_returns_no_rows_no_crash(tmp_path: Path) -> None:
    """An empty CSV file must not crash — returns a ParsedBOM with no rows."""
    path = _write(tmp_path, "empty.csv", "")

    result = parse_csv(path)

    assert result.rows == []
    assert result.source.format == FileFormat.CSV
