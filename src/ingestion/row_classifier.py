"""Lossless detection of non-data rows (page footers / headers / free-text notes).

This module NEVER drops a row. It only *tags* rows that look like a page footer,
header, or free-text note rather than a real BOM position, so a reviewer can
confirm. Both project guarantees are preserved by construction:

* Zero-data-loss — nothing is removed; the row stays in the table and in the
  position-reconciler set. Only an advisory flag is attached.
* Zero-false-green — this feeds NO scoring signal; classification is orthogonal
  to the traffic-light verdict.

Safety rule: a row that carries a valid position identifier is ALWAYS treated as
data and is never flagged. The position number is the strongest data-row signal,
so genuine rows cannot be mis-tagged. A row is only flagged when it lacks a
position AND shows an independent non-data signal (footer/header text, or — when
the table has a position column — a near-empty row).
"""

from __future__ import annotations

import re

# Header tokens that identify the position/detail column (incl. common foreign
# headers seen in customer BOMs). Mirrors _infer_anchor_column in pdf_parser but
# kept self-contained to avoid importing the heavy (fitz) parsing module.
_ANCHOR_KEYWORDS = (
    "pos", "position", "detail", "nr", "no", "num", "pozice", "index",
    "序号", "图号", "n°",
)

# A position-like token: optional leading/trailing letter around digits, with
# dotted/dashed/slashed segments. Matches "10", "1000", "1-01", "1.01", "A12",
# "10A". Does NOT match words ("Seite", "darf") or spaced phrases.
_POSITION_TOKEN = re.compile(r"^[A-Za-z]?\d+(?:[.\-/_]\d+)*[A-Za-z]?$")

_FOOTER_PATTERNS = (
    re.compile(r"(seite|page|p[áa]g|页|blatt|feuille)\s*\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\d+\s*(/|von|of|aus|sur)\s*\d+\b", re.IGNORECASE),  # "1 / 5", "1 von 5"
    re.compile(r"(©|copyright|all rights reserved|confidential|vertraulich)", re.IGNORECASE),
    re.compile(r"(gedruckt|printed|imprim|powered by|notta\.ai)", re.IGNORECASE),
    re.compile(r"\brev(ision)?\.?\s*[:#]?\s*[A-Za-z0-9]", re.IGNORECASE),
    re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b"),  # date stamp
    re.compile(r"(unkontrolliert|uncontrolled|kontrollierte kopie|controlled|受控|副本)", re.IGNORECASE),
)

# A page-number "n / m" pattern is only a footer signal in short, sparse text —
# guard against a dimension like "165 / 74" sitting in a wide data row.
_PAGE_RATIO_RE = _FOOTER_PATTERNS[1]


def _infer_anchor_column(columns: list[str]) -> str | None:
    for col in columns:
        normalized = re.sub(r"\s+", "", str(col)).lower()
        if any(keyword in normalized for keyword in _ANCHOR_KEYWORDS):
            return col
    return None


def _is_position_like(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    return bool(_POSITION_TOKEN.match(text)) and any(ch.isdigit() for ch in text)


def _looks_like_footer_text(text: str, *, sparse: bool) -> bool:
    for pattern in _FOOTER_PATTERNS:
        if pattern is _PAGE_RATIO_RE:
            # "n / m" alone is ambiguous (could be a dimension/fraction in a data
            # row) — only treat it as a footer signal in a near-empty row.
            if sparse and pattern.search(text):
                return True
            continue
        if pattern.search(text):
            return True
    return False


def classify_non_data_rows(
    rows: list[dict[str, str | None]],
    columns: list[str],
) -> dict[int, list[str]]:
    """Return {row_index: [reason_codes]} for rows that look like non-data.

    A row absent from the dict is a normal data row. The result is purely
    advisory — callers MUST NOT drop the flagged rows.
    """
    anchor = _infer_anchor_column(columns)
    has_anchor = anchor is not None
    flags: dict[int, list[str]] = {}

    for index, row in enumerate(rows):
        # Safety gate: a valid position identifier => always data.
        if has_anchor and _is_position_like(row.get(anchor)):
            continue

        nonempty = [
            str(value).strip()
            for value in row.values()
            if value is not None and str(value).strip()
        ]
        sparse = len(nonempty) <= 1
        joined = " ".join(nonempty)

        reasons: list[str] = []
        if joined and _looks_like_footer_text(joined, sparse=sparse):
            reasons.append("FOOTER_OR_HEADER_TEXT")
        # A near-empty row is only a meaningful non-data signal when the table
        # actually has a position column (so we KNOW the position is missing).
        if has_anchor and sparse:
            reasons.append("SPARSE_ROW")

        if reasons:
            flags[index] = ["NO_POSITION", *reasons]

    return flags
