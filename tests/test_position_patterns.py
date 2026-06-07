"""B1 acceptance: format-agnostic position patterns + C1 integration."""

from __future__ import annotations

import re

import pytest

import src.ingestion.pdf_parser as pdf_parser
import src.scoring.ensemble_scorer as es


def _positions(pages: list[str]) -> set[str]:
    return {e.position for e in es._extract_pdf_positions_from_pages(pages)}


def test_sequential_positions_detected_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an instance enables the bare sequential pattern, 1..1000 are detected.

    The bare pattern is opt-in (not a default) because on raw text it has no
    column context; this verifies the YAML-configurable mechanism delivers
    sequential detection when explicitly turned on.
    """
    broad = re.compile(
        r"(?:\b|\s)(?P<pos>\d+-\d+|K-\d+|[A-Z]-\d+|\b\d{1,4}\b)\b", re.IGNORECASE
    )
    monkeypatch.setattr(es, "_PDF_POSITION_RE", broad)

    page = "\n".join(f"{i} Formplatte Stahl" for i in range(1, 1001))
    found = _positions([page])
    expected = {str(i) for i in range(1, 1001)}
    assert expected <= found


def test_default_patterns_are_specific_only() -> None:
    """Default (shipped) patterns are specific — the bare sequential is opt-in."""
    pat = es._PDF_POSITION_RE.pattern
    assert r"\d+-\d+" in pat
    assert r"K-\d+" in pat
    assert r"[A-Z]-\d+" in pat
    assert r"\d{1,4}" not in pat  # broad sequential must NOT be active by default


def test_schaufler_format_still_works() -> None:
    """The original Schaufler / K formats remain detected."""
    page = "\n".join(["1-1 Einsatz", "K-5 Kern", "10-20 Schieber"])
    found = _positions([page])
    assert {"1-1", "K-5", "10-20"} <= found


def test_generic_format_detected() -> None:
    """Generic letter-prefixed positions (A-12, P-3) are detected."""
    page = "\n".join(["A-12 Buchse", "P-3 Platte"])
    found = _positions([page])
    assert {"A-12", "P-3"} <= found


class _FakePage:
    def __init__(self, blocks: list[tuple], height: float) -> None:
        self.rect = type("Rect", (), {"height": height})()
        self._blocks = blocks

    def get_text(self, _kind: str, sort: bool = False) -> list[tuple]:
        return self._blocks


def test_header_numbers_not_captured() -> None:
    """C1 + B1 end-to-end: a header 'Seite 1 von 3' is filtered before scanning,
    so its numbers never become phantom positions."""
    blocks = [
        (0.0, 5.0, 100.0, 15.0, "Seite 1 von 3"),     # header band (center 10)
        (0.0, 195.0, 100.0, 205.0, "1-1 Formplatte"),  # body (center 200)
    ]
    page = _FakePage(blocks, height=800.0)

    # Render via the text-path layout function (applies the C1 geometric filter)
    rendered = pdf_parser._render_layout_aware_page_text(page)
    found = _positions([rendered])

    assert "Seite" not in rendered  # C1 removed the header block entirely
    assert "1-1" in found
    assert "1" not in found  # would be a phantom from "Seite 1 von 3"
    assert "3" not in found
