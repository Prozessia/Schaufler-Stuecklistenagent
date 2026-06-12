"""Vision-based per-field counter-check service.

Provides an independent second GPT-4o Vision call that verifies one BOM field
value at a time against the original PDF page image.  This acts as the
forensic CHECK5 gate in the Triple-Lock verification pipeline.

Integration:
    - Wired into the pipeline via ``pipeline_runner.run_pipeline()`` (creates
      the ``VisionCounterCheckService`` instance) and consumed by
      ``ensemble_scorer.score_bom_async()`` for GREEN-candidate cells.
    - Fires only for cells that pass the deterministic pre-gate
      (``green_gate.can_be_green``) — i.e. potential GREEN promotions.

Prompt version: ``counter_check_v1``
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from src.core.models import SourceLocation
from src.llm.base import BaseLLM

logger = logging.getLogger(__name__)

PROMPT_VERSION = "counter_check_v1"

_COUNTER_CHECK_SYSTEM = (
    "You are a forensic OCR verifier for a single BOM field. "
    "Read exactly one field value from the provided PDF page image. "
    "Do not extract full tables and do not infer missing values. "
    "Return JSON only."
)

_COUNTER_CHECK_USER_TEMPLATE = """\
You receive ONE PDF page image.

Task:
- Verify only one target field and return only the observed value for that field.
- Ignore all other fields, rows, logos, and metadata.
- If the value cannot be read confidently, do not guess.

Target field:
- Field name: {target_field}
- Source column hint: {source_column}
- Page number: {page_number}
- Approximate bounding box hint (PDF points): {bbox_hint}

Rules:
1. Return status="found" only if the value is clearly readable.
2. Return status="ambiguous" if multiple candidates exist or confidence is low.
3. Return status="not_found" if no readable value can be identified.
4. Keep observed_value exactly as seen (no normalization).

Output JSON schema (no markdown):
{{
  "status": "found|ambiguous|not_found",
  "observed_value": "string or null",
  "confidence": 0.0,
  "evidence": "short reason"
}}
"""


@dataclass(slots=True)
class CounterCheckRequest:
    """Input contract for one independent counter-check."""

    job_id: str
    pdf_path: Path
    page_number: int
    target_field: str
    source_column: str
    primary_extracted_value: str | None
    source_location: SourceLocation | None = None


@dataclass(slots=True)
class BatchFieldRequest:
    """One field of a page-level batch verification (PERF-002)."""

    request_id: str
    target_field: str
    source_column: str
    primary_value: str | None
    bbox_hint: str = "unknown"


@dataclass(slots=True)
class CounterCheckResult:
    """Output contract to be stored in audit/counter-check fields."""

    passed: bool
    score: float
    reason: str
    notes: str
    secondary_value: str | None
    secondary_confidence: float
    prompt_version: str = PROMPT_VERSION


@dataclass(slots=True)
class _RawVisionVerdict:
    status: str
    observed_value: str | None
    confidence: float
    evidence: str


class JobPageRenderCache:
    """Per-job PDF page render cache.

    Guarantees:
    - Cache scope is one job.
    - A page is rendered at most once per job.
    - Memory can be released explicitly via close().
    """

    def __init__(self, job_id: str, pdf_path: Path, dpi: int = 250) -> None:
        self.job_id = job_id
        self.pdf_path = Path(pdf_path)
        self._dpi = dpi
        self._doc: fitz.Document | None = None
        self._page_cache: dict[int, str] = {}
        self._closed = False

    @property
    def cached_page_count(self) -> int:
        return len(self._page_cache)

    def get_page_image_b64(self, page_number: int) -> str:
        """Return base64 PNG for page_number (1-based), cached per job/page."""
        if self._closed:
            raise RuntimeError("JobPageRenderCache is closed")
        if page_number < 1:
            raise ValueError(f"Invalid page_number={page_number}")

        cached = self._page_cache.get(page_number)
        if cached is not None:
            return cached

        doc = self._ensure_doc_open()
        page_index = page_number - 1
        if page_index >= len(doc):
            raise ValueError(
                f"Page out of range: page={page_number}, total_pages={len(doc)}"
            )

        zoom = self._dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = doc.load_page(page_index).get_pixmap(matrix=matrix)
        image_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
        self._page_cache[page_number] = image_b64
        return image_b64

    def close(self) -> None:
        """Release all cached images and close file handles."""
        if self._closed:
            return
        self._page_cache.clear()
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._closed = True

    def _ensure_doc_open(self) -> fitz.Document:
        if self._doc is None:
            self._doc = fitz.open(self.pdf_path)
        return self._doc


class VisionCounterCheckService:
    """Independent counter-check service using a second Vision call.

    Not integrated yet. Planned usage:
    - one service instance for pipeline execution
    - verify() for Green-gate candidates
    - release_job(job_id) when a job is complete
    """

    def __init__(
        self,
        llm: BaseLLM,
        *,
        dpi: int = 250,
        max_tokens: int = 900,
    ) -> None:
        self._llm = llm
        self._dpi = dpi
        self._max_tokens = max_tokens
        self._job_caches: dict[str, JobPageRenderCache] = {}

    async def verify(self, request: CounterCheckRequest) -> CounterCheckResult:
        """Run one isolated verification call and compare with primary value."""
        if request.page_number < 1:
            return CounterCheckResult(
                passed=False,
                score=0.0,
                reason="invalid_page_number",
                notes=f"page_number={request.page_number}",
                secondary_value=None,
                secondary_confidence=0.0,
            )

        cache = self._get_or_create_job_cache(request.job_id, request.pdf_path)
        image_b64 = cache.get_page_image_b64(request.page_number)

        prompt = _build_counter_check_prompt(request)
        # NOTE: complete_with_image always uses GPT-4o (model_main), not
        # GPT-4o-mini.  This is intentional — the Vision counter-check is a
        # per-field forensic verification step where accuracy is prioritised
        # over cost.  GPT-4o-mini is not used here because image-based calls
        # require the full-capability model for reliable single-value reading.
        response = await self._llm.complete_with_image(
            system=_COUNTER_CHECK_SYSTEM,
            user=prompt,
            image_b64=image_b64,
            json_mode=True,
            temperature=0.0,
            max_tokens=self._max_tokens,
        )

        verdict = _parse_counter_check_response(response.content)
        passed = _counter_check_passed(verdict, request.primary_extracted_value)
        compare_note = _build_compare_note(verdict, request.primary_extracted_value)

        return CounterCheckResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=("match" if passed else "mismatch_or_unreadable"),
            notes=compare_note,
            secondary_value=verdict.observed_value,
            secondary_confidence=verdict.confidence,
        )

    async def verify_fields(
        self,
        job_id: str,
        pdf_path: Path,
        page_number: int,
        requests: list[BatchFieldRequest],
    ) -> dict[str, CounterCheckResult]:
        """Verify MANY fields of one page with a single Vision call (PERF-002).

        One call per page instead of one per cell — this is what makes the
        counter-check economically usable. Fields the model does not answer
        count as failed (conservative). Returns results keyed by request_id.
        """
        if not requests:
            return {}
        if page_number < 1:
            return {
                r.request_id: CounterCheckResult(
                    passed=False,
                    score=0.0,
                    reason="invalid_page_number",
                    notes=f"page_number={page_number}",
                    secondary_value=None,
                    secondary_confidence=0.0,
                )
                for r in requests
            }

        cache = self._get_or_create_job_cache(job_id, pdf_path)
        image_b64 = cache.get_page_image_b64(page_number)

        field_lines = "\n".join(
            f'- id="{r.request_id}": Feld "{r.target_field}" '
            f'(Spalten-Hinweis: {r.source_column or "unbekannt"}, '
            f"Bbox-Hinweis: {r.bbox_hint})"
            for r in requests
        )
        prompt = (
            "Du bekommst EIN Seitenbild einer technischen Stückliste "
            f"(Seite {page_number}).\n\n"
            "Lies für JEDES der folgenden Felder den sichtbaren Wert ab. "
            "Nicht raten; nicht normalisieren; keine anderen Felder.\n\n"
            f"Felder:\n{field_lines}\n\n"
            "Regeln:\n"
            '1. status="found" nur bei klar lesbarem Wert.\n'
            '2. status="ambiguous" bei mehreren Kandidaten oder geringer Sicherheit.\n'
            '3. status="not_found" wenn kein Wert identifizierbar ist.\n\n'
            "Antwortformat (NUR JSON):\n"
            '{"results": [{"id": "...", "status": "found|ambiguous|not_found", '
            '"observed_value": "string oder null", "confidence": 0.0, '
            '"evidence": "kurzer Grund"}]}'
        )

        response = await self._llm.complete_with_image(
            system=_COUNTER_CHECK_SYSTEM,
            user=prompt,
            image_b64=image_b64,
            json_mode=True,
            temperature=0.0,
            max_tokens=max(self._max_tokens, 220 * len(requests) + 300),
        )

        payload = _safe_json_loads(response.content)
        raw_by_id: dict[str, dict] = {}
        if isinstance(payload, dict):
            for entry in payload.get("results") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    raw_by_id[str(entry["id"])] = entry

        results: dict[str, CounterCheckResult] = {}
        for request in requests:
            entry = raw_by_id.get(request.request_id)
            if entry is None:
                results[request.request_id] = CounterCheckResult(
                    passed=False,
                    score=0.0,
                    reason="missing_in_batch_response",
                    notes="counter_check_batch_no_answer",
                    secondary_value=None,
                    secondary_confidence=0.0,
                )
                continue

            verdict = _verdict_from_entry(entry)
            passed = _counter_check_passed(verdict, request.primary_value)
            results[request.request_id] = CounterCheckResult(
                passed=passed,
                score=1.0 if passed else 0.0,
                reason=("match" if passed else "mismatch_or_unreadable"),
                notes=_build_compare_note(verdict, request.primary_value),
                secondary_value=verdict.observed_value,
                secondary_confidence=verdict.confidence,
            )
        return results

    def release_job(self, job_id: str) -> None:
        """Release one job-scoped cache (required to avoid memory leaks)."""
        cache = self._job_caches.pop(job_id, None)
        if cache is not None:
            cache.close()

    def close(self) -> None:
        """Release all job caches."""
        for cache in self._job_caches.values():
            cache.close()
        self._job_caches.clear()

    def _get_or_create_job_cache(
        self,
        job_id: str,
        pdf_path: Path,
    ) -> JobPageRenderCache:
        cache = self._job_caches.get(job_id)
        if cache is None:
            cache = JobPageRenderCache(job_id=job_id, pdf_path=pdf_path, dpi=self._dpi)
            self._job_caches[job_id] = cache
            return cache

        # Protect against accidental path mix-up for the same job id.
        if cache.pdf_path != Path(pdf_path):
            logger.warning(
                "Replacing cache for job_id=%s due to differing pdf_path (%s -> %s)",
                job_id,
                cache.pdf_path,
                pdf_path,
            )
            cache.close()
            cache = JobPageRenderCache(job_id=job_id, pdf_path=pdf_path, dpi=self._dpi)
            self._job_caches[job_id] = cache

        return cache


def _build_counter_check_prompt(request: CounterCheckRequest) -> str:
    bbox_hint = "unknown"
    if request.source_location and request.source_location.bbox:
        bbox_hint = str(request.source_location.bbox)

    return _COUNTER_CHECK_USER_TEMPLATE.format(
        target_field=request.target_field,
        source_column=request.source_column or "unknown",
        page_number=request.page_number,
        bbox_hint=bbox_hint,
    )


def _verdict_from_entry(entry: dict) -> _RawVisionVerdict:
    """Build a verdict from one batch-result entry (same semantics as single)."""
    status = str(entry.get("status", "ambiguous")).strip().lower()
    if status not in {"found", "ambiguous", "not_found"}:
        status = "ambiguous"
    observed_raw = entry.get("observed_value")
    observed_value = None if observed_raw is None else str(observed_raw).strip()
    if observed_value == "":
        observed_value = None
    return _RawVisionVerdict(
        status=status,
        observed_value=observed_value,
        confidence=_clamp(entry.get("confidence", 0.0)),
        evidence=str(entry.get("evidence", "")).strip(),
    )


def _parse_counter_check_response(content: str) -> _RawVisionVerdict:
    payload = _safe_json_loads(content)
    if not isinstance(payload, dict):
        return _RawVisionVerdict(
            status="ambiguous",
            observed_value=None,
            confidence=0.0,
            evidence="invalid_json",
        )

    status = str(payload.get("status", "ambiguous")).strip().lower()
    if status not in {"found", "ambiguous", "not_found"}:
        status = "ambiguous"

    observed_raw = payload.get("observed_value")
    observed_value = None if observed_raw is None else str(observed_raw).strip()
    if observed_value == "":
        observed_value = None

    confidence = _clamp(payload.get("confidence", 0.0))
    evidence = str(payload.get("evidence", "")).strip()

    return _RawVisionVerdict(
        status=status,
        observed_value=observed_value,
        confidence=confidence,
        evidence=evidence,
    )


def _safe_json_loads(content: str) -> dict | list | None:
    text = (content or "").strip()
    if not text:
        return None

    candidates: list[str] = [text]
    if text.startswith("```"):
        stripped = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        if stripped:
            candidates.append(stripped)

    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start != -1 and obj_end > obj_start:
        candidates.append(text[obj_start : obj_end + 1])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        normalized = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            continue

    return None


def _counter_check_passed(
    verdict: _RawVisionVerdict,
    primary_value: str | None,
) -> bool:
    if verdict.status != "found":
        return False
    if not verdict.observed_value or not primary_value:
        return False
    return _normalize_for_compare(verdict.observed_value) == _normalize_for_compare(
        primary_value
    )


def _build_compare_note(
    verdict: _RawVisionVerdict,
    primary_value: str | None,
) -> str:
    primary = primary_value or ""
    secondary = verdict.observed_value or ""
    return (
        f"status={verdict.status}; "
        f"primary='{primary}'; "
        f"secondary='{secondary}'; "
        f"confidence={verdict.confidence:.2f}; "
        f"evidence={verdict.evidence}"
    )


def _normalize_for_compare(value: str) -> str:
    normalized = value.strip().casefold()
    normalized = re.sub(r"\s+", " ", normalized)

    # Numeric harmonization for common OCR differences like comma vs dot.
    if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", normalized):
        normalized = normalized.replace(",", ".")

    return normalized


def _clamp(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric
