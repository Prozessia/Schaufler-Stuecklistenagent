"""Structure Normalizer — unified entry point for parsing any BOM file.

Routes files to the right parser and normalizes the output.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.core.models import FileFormat, ParsedBOM
from src.ingestion.excel_parser import parse_excel
from src.ingestion.file_router import detect_format, infer_customer
from src.ingestion.pdf_common import PasswordProtectedPdfError
from src.llm.base import BaseLLM

logger = logging.getLogger(__name__)


async def parse_file(filepath: Path | str, llm: BaseLLM | None = None) -> ParsedBOM:
    """Parse any supported BOM file and return a normalized ParsedBOM.

    This is the main entry point for Layer 1.
    For PDFs, text-layer documents prefer PyMuPDF text extraction + GPT-4o-mini.
    GPT-4o Vision is reserved for image-based scans without a usable text layer.
    For Excel/CSV, the llm parameter is ignored.
    """
    filepath = Path(filepath)
    fmt = detect_format(filepath)

    logger.info("Parsing %s (format=%s)", filepath.name, fmt.value)

    if fmt == FileFormat.EXCEL:
        result = parse_excel(filepath)
    elif fmt == FileFormat.PDF:
        if llm is None:
            logger.info(
                "No LLM client provided for %s, using legacy PDF parser",
                filepath.name,
            )
            from src.ingestion.pdf_parser_legacy import parse_pdf as parse_pdf_legacy

            result = parse_pdf_legacy(filepath)
        else:
            from src.ingestion.coordinate_table import reconstruct_table
            from src.ingestion.pdf_common import ExtractionError
            from src.ingestion.pdf_parser import pdf_has_text_layer
            from src.ingestion.pdf_parser import parse_pdf as parse_pdf_vision
            from src.ingestion.pdf_parser_legacy import parse_pdf as parse_pdf_legacy

            try:
                try:
                    has_text_layer = pdf_has_text_layer(filepath)
                except PasswordProtectedPdfError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Could not determine text-layer status for %s, defaulting to scan path: %s",
                        filepath.name,
                        exc,
                    )
                    has_text_layer = False

                if has_text_layer:
                    # RB-1: deterministic coordinate reconstruction (no LLM call in
                    # ingestion). If it declines (rotated/transposed layout with no
                    # detectable header, e.g. GF), fall back to Vision — its only
                    # real chance — instead of emitting confident garbage.
                    try:
                        logger.info(
                            "PDF %s has a text layer; using deterministic "
                            "coordinate reconstruction (RB-1)",
                            filepath.name,
                        )
                        result = await reconstruct_table(filepath, llm)
                    except ExtractionError as exc:
                        logger.info(
                            "Deterministic reconstruction declined %s (%s); "
                            "using Vision fallback",
                            filepath.name,
                            exc,
                        )
                        result = await parse_pdf_vision(filepath, llm)
                else:
                    logger.info(
                        "PDF %s has no usable text layer; using GPT-4o Vision fallback",
                        filepath.name,
                    )
                    result = await parse_pdf_vision(filepath, llm)
            except PasswordProtectedPdfError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Primary PDF parsing failed for %s: %s", filepath.name, exc
                )
                result = parse_pdf_legacy(filepath)
                result.metadata["primary_parse_failure_reason"] = str(exc)
                if not locals().get("has_text_layer", False):
                    result.metadata["vision_fallback_reason"] = str(exc)
    else:
        raise ValueError(f"Unsupported file format: {fmt} for {filepath}")

    # Post-processing: normalize
    result = _normalize(result)

    logger.info(
        "Parsed %s: %d headers, %d rows, confidence=%.2f",
        filepath.name,
        result.total_columns,
        result.total_rows,
        result.source.extraction_confidence,
    )

    return result


def parse_file_sync(filepath: Path | str, llm: BaseLLM | None = None) -> ParsedBOM:
    """Synchronous wrapper around parse_file for scripts and sync tests."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(parse_file(filepath, llm=llm))

    raise RuntimeError(
        "parse_file_sync cannot run inside an active event loop; use 'await parse_file(...)'."
    )


def parse_directory(dirpath: Path | str, recursive: bool = True) -> list[ParsedBOM]:
    """Parse all supported files in a directory.

    Returns list of ParsedBOM results, one per file.
    """
    dirpath = Path(dirpath)
    results: list[ParsedBOM] = []

    glob_pattern = "**/*" if recursive else "*"
    for filepath in sorted(dirpath.glob(glob_pattern)):
        if not filepath.is_file():
            continue

        ext = filepath.suffix.lower()
        if ext not in (".xlsx", ".xls", ".pdf", ".csv"):
            continue

        try:
            bom = parse_file_sync(filepath)
            results.append(bom)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse %s: %s", filepath, exc)
            results.append(
                ParsedBOM(
                    source=_error_metadata(filepath),
                    metadata={"error": str(exc)},
                )
            )

    return results


def _normalize(bom: ParsedBOM) -> ParsedBOM:
    """Final normalization pass on a parsed BOM."""
    # Strip excessive whitespace/newlines from all values
    clean_rows = []
    for row in bom.rows:
        clean: dict[str, str | None] = {}
        for key, val in row.items():
            if isinstance(val, str):
                # Collapse newlines to spaces, strip
                val = " ".join(val.split())
                val = val.strip() if val.strip() else None
            clean[key] = val
        clean_rows.append(clean)
    bom.rows = clean_rows

    # Clean headers too (collapse newlines)
    bom.headers = [" ".join(h.split()) for h in bom.headers]

    return bom


def _error_metadata(filepath: Path):
    from src.core.models import SourceMetadata

    return SourceMetadata(
        filename=filepath.name,
        filepath=str(filepath),
        customer=infer_customer(filepath),
        format=detect_format(filepath) if filepath.exists() else FileFormat.UNKNOWN,
        extraction_method=None,
        extraction_confidence=0.0,
    )
