"""Deterministic PDF value extraction for triple-check scoring."""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.core.models import ExtractionMethod, SourceLocation, TransformationResult


@dataclass(slots=True)
class PDFExtractionResult:
    found: bool
    extracted_value: str | None
    confidence: float
    source_location: str
    reason: str
    # GREEN-RECOVERY P0/P1: the parser's column-resolution quality for this cell.
    # "row_fallback" means NO x-corridor could be isolated, so `extracted_value`
    # is the WHOLE row line — not column-scoped evidence. The scorer/comparator
    # must therefore confirm by in-row containment, never hard-veto on equality.
    match_type: str = ""


@dataclass(slots=True)
class _DocumentAnchorContext:
    context_text: str
    anchor_field: str
    anchor_value: str
    line_index: int


class PDFValueExtractor:
    """Extract values from parser-provided PDF coordinate evidence."""

    _TEXT_PATH_BASE_CONFIDENCE = 0.95
    _TEXT_PATH_JSON_REPAIRED_CONFIDENCE = 0.86
    _TEXT_PATH_UNCERTAIN_FIELD_CONFIDENCE = 0.68
    _GLOBAL_TEXT_REASON = "global_text_row_anchor"
    _ROW_ANCHOR_FIELDS = ("Detail Number", "Customer Part Number")
    _QUANTITY_FIELDS = {"Design Count", "Spare Count"}

    _CONFIDENCE_BY_MATCH_TYPE = {
        "column_corridor": 0.98,
        "column_estimate": 0.88,
        "row_fallback": 0.82,
    }

    def extract_value_for_column(
        self,
        source_column_name: str,
        source_row_index: int,
        pdf_document: TransformationResult,
        target_field: str = "",
        mapped_value: str | None = None,
    ) -> PDFExtractionResult:
        if not pdf_document.source_is_pdf:
            return PDFExtractionResult(
                found=False,
                extracted_value=None,
                confidence=0.0,
                source_location="",
                reason="no_pdf_source",
            )

        if not pdf_document.has_text_layer:
            return PDFExtractionResult(
                found=False,
                extracted_value=None,
                confidence=0.0,
                source_location="",
                reason="no_pdf_text_layer",
            )

        if not source_column_name:
            return PDFExtractionResult(
                found=False,
                extracted_value=None,
                confidence=0.0,
                source_location="",
                reason="missing_source_column",
            )

        row_locations = pdf_document.source_locations.get(source_row_index, {})
        location = row_locations.get(
            source_column_name
        ) or self._find_case_insensitive_location(row_locations, source_column_name)

        if not location or not (location.text or "").strip():
            fallback = self._extract_from_document_text_layer(
                source_row_index=source_row_index,
                source_column_name=source_column_name,
                pdf_document=pdf_document,
                target_field=target_field,
                mapped_value=mapped_value,
            )
            if fallback is not None:
                return fallback

            return PDFExtractionResult(
                found=False,
                extracted_value=None,
                confidence=0.0,
                source_location="",
                reason="no_coordinate_match",
            )

        extracted = (location.text or "").strip()
        confidence = self._resolve_confidence(
            pdf_document=pdf_document,
            source_row_index=source_row_index,
            source_column_name=source_column_name,
            location=location,
        )
        source_location = self._format_source_location(location)
        reason = self._resolve_reason(
            pdf_document=pdf_document,
            source_row_index=source_row_index,
            source_column_name=source_column_name,
        )

        return PDFExtractionResult(
            found=True,
            extracted_value=extracted,
            confidence=confidence,
            source_location=source_location,
            reason=reason,
            match_type=location.match_type or "",
        )

    def _extract_from_document_text_layer(
        self,
        *,
        source_row_index: int,
        source_column_name: str,
        pdf_document: TransformationResult,
        target_field: str,
        mapped_value: str | None,
    ) -> PDFExtractionResult | None:
        mapped_text = (mapped_value or "").strip()
        if not target_field or not mapped_text:
            return None

        confidence = self._resolve_document_text_confidence(
            pdf_document=pdf_document,
            source_row_index=source_row_index,
            source_column_name=source_column_name,
        )
        if confidence < 0.90:
            return None

        anchor_context = self._find_anchor_context(
            pdf_document=pdf_document,
            source_row_index=source_row_index,
            target_field=target_field,
            mapped_value=mapped_text,
        )
        if anchor_context is None:
            return None

        extracted_value = self._extract_value_from_anchor_context(
            target_field=target_field,
            mapped_value=mapped_text,
            anchor_context=anchor_context,
        )
        if extracted_value is None:
            return None

        return PDFExtractionResult(
            found=True,
            extracted_value=extracted_value,
            confidence=confidence,
            source_location=(
                "document_text_layer;"
                f"anchor_field={anchor_context.anchor_field};"
                f"anchor_value={anchor_context.anchor_value};"
                f"line={anchor_context.line_index + 1}"
            ),
            reason=self._GLOBAL_TEXT_REASON,
        )

    def _resolve_confidence(
        self,
        *,
        pdf_document: TransformationResult,
        source_row_index: int,
        source_column_name: str,
        location: SourceLocation,
    ) -> float:
        if pdf_document.extraction_method != ExtractionMethod.PYMUPDF_TEXT:
            return self._CONFIDENCE_BY_MATCH_TYPE.get(location.match_type, 0.72)

        confidence = pdf_document.source_extraction_confidence
        if confidence <= 0.0:
            confidence = self._TEXT_PATH_BASE_CONFIDENCE

        if pdf_document.extraction_json_repaired:
            confidence = min(confidence, self._TEXT_PATH_JSON_REPAIRED_CONFIDENCE)

        uncertain_columns = pdf_document.extraction_uncertain_cells.get(
            source_row_index,
            [],
        )
        if source_column_name in uncertain_columns:
            confidence = min(confidence, self._TEXT_PATH_UNCERTAIN_FIELD_CONFIDENCE)

        return max(0.0, min(confidence, 1.0))

    def _resolve_document_text_confidence(
        self,
        *,
        pdf_document: TransformationResult,
        source_row_index: int,
        source_column_name: str,
    ) -> float:
        if pdf_document.extraction_method != ExtractionMethod.PYMUPDF_TEXT:
            return max(0.0, min(float(pdf_document.source_extraction_confidence), 1.0))

        confidence = pdf_document.source_extraction_confidence
        if confidence <= 0.0:
            confidence = self._TEXT_PATH_BASE_CONFIDENCE

        if pdf_document.extraction_json_repaired:
            confidence = min(confidence, self._TEXT_PATH_JSON_REPAIRED_CONFIDENCE)

        uncertain_columns = pdf_document.extraction_uncertain_cells.get(
            source_row_index,
            [],
        )
        if source_column_name in uncertain_columns:
            confidence = min(confidence, self._TEXT_PATH_UNCERTAIN_FIELD_CONFIDENCE)

        return max(0.0, min(confidence, 1.0))

    def _resolve_reason(
        self,
        *,
        pdf_document: TransformationResult,
        source_row_index: int,
        source_column_name: str,
    ) -> str:
        if pdf_document.extraction_method != ExtractionMethod.PYMUPDF_TEXT:
            return "ok"

        if source_column_name in pdf_document.extraction_uncertain_cells.get(
            source_row_index,
            [],
        ):
            return "llm_uncertain_field"
        if pdf_document.extraction_json_repaired:
            return "json_repaired"
        if pdf_document.check2_reason:
            return pdf_document.check2_reason
        return "text_layer_direct"

    def _find_anchor_context(
        self,
        *,
        pdf_document: TransformationResult,
        source_row_index: int,
        target_field: str,
        mapped_value: str,
    ) -> _DocumentAnchorContext | None:
        document_text = (pdf_document.document_text_layer or "").strip()
        if not document_text:
            return None

        row = self._find_row(pdf_document, source_row_index)
        if row is None:
            return None

        anchors = self._build_anchor_candidates(
            row=row,
            target_field=target_field,
            mapped_value=mapped_value,
        )
        if not anchors:
            return None

        lines = _document_text_lines(document_text)
        if not lines:
            return None

        best_match: _DocumentAnchorContext | None = None
        best_score: tuple[int, int, int] | None = None
        for anchor_field, anchor_value in anchors:
            anchor_core = _normalize_relaxed(anchor_value)
            if not anchor_core:
                continue

            for line_index, context_text in _iter_global_anchor_contexts(
                document_text,
                anchor_value,
            ):
                context_core = _normalize_relaxed(context_text)
                if not context_core or anchor_core not in context_core:
                    continue

                score = (len(anchor_core), 2, -line_index)
                if best_score is None or score > best_score:
                    best_score = score
                    best_match = _DocumentAnchorContext(
                        context_text=context_text,
                        anchor_field=anchor_field,
                        anchor_value=anchor_value,
                        line_index=line_index,
                    )

            for line_index, context_text, same_line in _iter_context_windows(lines):
                context_core = _normalize_relaxed(context_text)
                if not context_core or anchor_core not in context_core:
                    continue

                score = (len(anchor_core), 1 if same_line else 0, -line_index)
                if best_score is None or score > best_score:
                    best_score = score
                    best_match = _DocumentAnchorContext(
                        context_text=context_text,
                        anchor_field=anchor_field,
                        anchor_value=anchor_value,
                        line_index=line_index,
                    )

        return best_match

    def _extract_value_from_anchor_context(
        self,
        *,
        target_field: str,
        mapped_value: str,
        anchor_context: _DocumentAnchorContext,
    ) -> str | None:
        context_text = anchor_context.context_text

        if target_field in self._QUANTITY_FIELDS:
            return self._extract_quantity_value(
                mapped_value=mapped_value,
                context_text=context_text,
                anchor_value=anchor_context.anchor_value,
            )

        if _context_contains_expected_value(mapped_value, context_text):
            return mapped_value

        return None

    def _extract_quantity_value(
        self,
        *,
        mapped_value: str,
        context_text: str,
        anchor_value: str,
    ) -> str | None:
        """Quantity from the anchor context — WITHOUT confirmation bias (BUG-002).

        The old logic returned the EXPECTED value whenever it appeared anywhere
        among the context numbers (dimensions, positions, material numbers all
        qualify) — that confirmed wrong quantities. Now the context must contain
        exactly ONE distinct quantity-shaped candidate; anything ambiguous
        returns None (→ extraction missing → UNCERTAIN, never GREEN).
        """
        expected_int = _parse_quantity_int(mapped_value)
        if expected_int is None:
            return None

        anchor_int = _parse_quantity_int(anchor_value)
        quantity_text = _extract_text_after_anchor(context_text, anchor_value)
        numeric_candidates = _extract_quantity_candidates(quantity_text)
        distinct = {
            candidate
            for candidate in numeric_candidates
            if anchor_int is None or candidate != anchor_int
        }

        if len(distinct) == 1:
            return str(next(iter(distinct)))

        return None

    @staticmethod
    def _find_row(
        pdf_document: TransformationResult,
        source_row_index: int,
    ):
        for row in pdf_document.rows:
            if row.row_index == source_row_index:
                return row
        return None

    def _build_anchor_candidates(
        self,
        *,
        row,
        target_field: str,
        mapped_value: str,
    ) -> list[tuple[str, str]]:
        anchors: list[tuple[str, str]] = []
        for field_name in self._ROW_ANCHOR_FIELDS:
            cell = row.get_cell(field_name)
            cell_value = (cell.transformed_value or "").strip() if cell else ""
            if cell_value:
                anchors.append((field_name, cell_value))

        if target_field in self._ROW_ANCHOR_FIELDS and mapped_value:
            current_anchor = (target_field, mapped_value)
            if current_anchor not in anchors:
                anchors.insert(0, current_anchor)

        return anchors

    @staticmethod
    def _find_case_insensitive_location(
        row_locations: dict[str, SourceLocation],
        source_column_name: str,
    ) -> SourceLocation | None:
        wanted = source_column_name.casefold()
        for key, location in row_locations.items():
            if key.casefold() == wanted:
                return location
        return None

    @staticmethod
    def _format_source_location(location: SourceLocation) -> str:
        page = f"page={location.page}" if location.page is not None else "page=?"
        bbox = f"bbox={location.bbox}" if location.bbox else "bbox=[]"
        match = f"match={location.match_type or 'unknown'}"
        return f"{page};{bbox};{match}"


def _document_text_lines(document_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in document_text.splitlines():
        cleaned = " ".join(raw_line.strip().split())
        if not cleaned or cleaned == "--- PAGE BREAK ---":
            continue
        lines.append(cleaned)
    return lines


def _iter_context_windows(lines: list[str]):
    seen: set[tuple[int, int]] = set()
    for index, line in enumerate(lines):
        candidates = [(index, index, line, True)]
        if index + 1 < len(lines):
            candidates.append((index, index + 1, f"{line} {lines[index + 1]}", False))
        if index > 0:
            candidates.append((index - 1, index, f"{lines[index - 1]} {line}", False))
        if 0 < index < len(lines) - 1:
            candidates.append(
                (
                    index - 1,
                    index + 1,
                    f"{lines[index - 1]} {line} {lines[index + 1]}",
                    False,
                )
            )

        for start, end, context_text, same_line in candidates:
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            yield start, context_text, same_line


def _iter_global_anchor_contexts(document_text: str, anchor_value: str):
    pattern = _build_anchor_pattern(anchor_value)
    if pattern is None:
        return

    for match in pattern.finditer(document_text or ""):
        start = match.start()
        end = match.end()
        line_index = document_text.count("\n", 0, start)
        context_text = _extract_match_context(document_text, start, end)
        if context_text:
            yield line_index, context_text


def _build_anchor_pattern(anchor_value: str):
    parts = [part for part in re.split(r"[-_/\s]+", anchor_value.strip()) if part]
    if not parts:
        return None

    separator = r"[\s\-_/]*"
    body = separator.join(re.escape(part) for part in parts)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def _extract_match_context(document_text: str, start: int, end: int) -> str:
    line_start = document_text.rfind("\n", 0, start)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1

    previous_line_start = document_text.rfind("\n", 0, max(0, line_start - 1))
    context_start = 0 if previous_line_start == -1 else previous_line_start + 1

    line_end = document_text.find("\n", end)
    if line_end == -1:
        line_end = len(document_text)

    next_line_end = document_text.find("\n", line_end + 1)
    context_end = len(document_text) if next_line_end == -1 else next_line_end

    return " ".join(document_text[context_start:context_end].split())


def _extract_text_after_anchor(context_text: str, anchor_value: str) -> str:
    pattern = _build_anchor_pattern(anchor_value)
    if pattern is None:
        return context_text

    match = pattern.search(context_text or "")
    if not match:
        return context_text
    return context_text[match.end() :]


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().casefold().split())


def _normalize_relaxed(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value))


def _context_contains_expected_value(mapped_value: str, context_text: str) -> bool:
    """Token-bounded containment check (BUG-001).

    The old substring test confirmed values across token/cell boundaries
    ("2" inside "3520", part numbers spanning two cells). Now the value must
    appear as a whole token sequence (flexible separators, hard alnum
    boundaries), and short pure-numeric values are never confirmable by mere
    presence — a 1-3 digit number occurs somewhere in almost every BOM window.
    """
    text = (mapped_value or "").strip()
    if not text or not (context_text or "").strip():
        return False

    expected_core = _normalize_relaxed(text)
    if not expected_core:
        return False
    if expected_core.isdigit() and len(expected_core) < 4:
        return False

    pattern = _build_anchor_pattern(text)
    return bool(pattern and pattern.search(context_text))


# BUG-002: a quantity token is a bare integer with at most a known unit suffix.
# Mixed tokens ("4x10" → 410, "M12" → 12) must NOT collapse into integers.
_QUANTITY_TOKEN_RE = re.compile(
    r"[-+]?(\d{1,4})(?:[.,]0+)?(?:\s*(?:stk|stck|pcs|pc|ea|x))?",
    re.IGNORECASE,
)


def _parse_quantity_int(value: str | None) -> int | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned:
        return None

    match = _QUANTITY_TOKEN_RE.fullmatch(cleaned)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_quantity_candidates(context_text: str) -> list[int]:
    candidates: list[int] = []
    for token in re.findall(r"[^\s|]+", context_text or ""):
        parsed = _parse_quantity_int(token)
        if parsed is not None:
            candidates.append(parsed)
    return candidates
