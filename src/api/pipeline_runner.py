"""Background pipeline runner — processes a BOM file through all layers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from src.api.job_store import job_store
from src.export.excel_exporter import export_to_excel
from src.ingestion.structure_normalizer import parse_file
from src.mapping.llm_column_mapper import map_columns
from src.mapping.mapping_validator import ValidationResult, validate_mapping
from src.mapping.schema_registry import load_schema
from src.reconciliation.position_reconciler import reconcile_positions
from src.scoring.ensemble_scorer import score_bom_async
from src.scoring.threshold_manager import load_scoring_config
from src.scoring.vision_verifier import VisionCounterCheckService
from src.transform.cross_validator import cross_validate
from src.transform.pipeline import transform_bom

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = _PROJECT_ROOT / "data" / "exports"


def _mark_job_failed(job_id: str, stage: str, error: str) -> None:
    message = f"{stage} failed: {error}" if stage else error
    try:
        job_store.update(job_id, status="failed", error=message)
    except Exception:  # noqa: BLE001
        logger.exception("Job %s: could not persist failure state", job_id)


def _job_timeout_seconds() -> float:
    """Return the job timeout in seconds from JOB_TIMEOUT_SECONDS env (default 1800, min 60)."""
    try:
        value = float(os.environ.get("JOB_TIMEOUT_SECONDS", "1800"))
    except (ValueError, TypeError):
        value = 1800.0
    return max(60.0, value)


async def run_pipeline(job_id: str) -> None:
    """Run the full BOM processing pipeline for a job."""
    job = job_store.get(job_id)
    if not job:
        return

    counter_check_service: VisionCounterCheckService | None = None
    stage = "starting pipeline"
    t_start = time.perf_counter()

    # Initialize LLM before the timeout wrapper so a failed init doesn't burn
    # timeout budget and the finally block can still clean up the service.
    try:
        job_store.update(job_id, status="processing", progress=0.1)

        stage = "LLM initialization"
        logger.info("Job %s: Initializing LLM", job_id)
        try:
            from src.llm.azure_openai import AzureOpenAILLM

            llm = AzureOpenAILLM()
            counter_check_service = VisionCounterCheckService(llm)
        except (ImportError, EnvironmentError, TypeError, ValueError) as e:
            _mark_job_failed(job_id, stage, str(e))
            return

        # --- inner pipeline wrapped with a wall-clock timeout ---
        async def _run_inner() -> None:
            nonlocal stage

            # 1. Parse (async for PDFs with Vision)
            stage = "parsing source file"
            logger.info("Job %s: Parsing %s", job_id, job.filename)

            def _progress_cb(done: int, total: int) -> None:
                try:
                    job_store.update(
                        job_id,
                        progress=0.1 + 0.2 * (done / max(total, 1)),
                    )
                except Exception:  # noqa: BLE001
                    pass

            bom = await parse_file(job.filepath, llm=llm, progress_callback=_progress_cb)
            if not bom.headers or not bom.rows:
                job_store.update(
                    job_id, status="failed", error="Could not parse file — no data found"
                )
                return
            # User-supplied customer has priority over the inferred one from parsing.
            if job.customer:
                bom.source.customer = job.customer
                job_store.update(job_id, progress=0.3, customer=job.customer)
            else:
                job_store.update(
                    job_id, progress=0.3, customer=bom.source.customer or ""
                )

            # 2. Map columns (needs LLM)
            stage = "mapping columns"
            logger.info("Job %s: Mapping columns", job_id)
            schema = load_schema()

            mapping = await map_columns(bom, llm, schema)
            if mapping.mapped_count == 0:
                job_store.update(
                    job_id,
                    status="failed",
                    error=(mapping.notes.strip() or "No column mappings found"),
                )
                return

            # 2b. Validate mapping (fail-closed middleware)
            # Blocking validator errors are enforced by the scorer as RED.
            stage = "mapping validation"
            mapping_validation: ValidationResult | None = None
            try:
                mapping_validation = validate_mapping(mapping, bom, schema)
                logger.info(
                    "Job %s: Mapping validation — %d errors, %d warnings, %d info",
                    job_id,
                    mapping_validation.error_count,
                    mapping_validation.warning_count,
                    len(mapping_validation.issues)
                    - mapping_validation.error_count
                    - mapping_validation.warning_count,
                )

                # Apply validator-adjusted mappings (e.g. duplicate demotions)
                # before entering transform/scoring.
                mapping = mapping.model_copy(
                    update={"mappings": list(mapping_validation.adjusted_mappings)}
                )
            except (ValueError, KeyError, AttributeError) as e:
                _mark_job_failed(job_id, stage, str(e))
                return

            job_store.update(job_id, progress=0.5)

            # 3. Transform
            stage = "value transformation"
            logger.info("Job %s: Transforming values", job_id)
            transform_result = transform_bom(bom, mapping, schema)
            job_store.update(job_id, progress=0.7)

            # 3b. Reconcile positions (B2) — re-inject PDF-only positions as MISSING
            # and set the master-set count so the zero-data-loss guard becomes sharp.
            stage = "position reconciliation"
            transform_result = reconcile_positions(
                transform_result,
                bom.raw_pdf_positions,
                schema,
                pdf_row_bands=bom.pdf_row_bands,
                raw_pdf_position_counts=bom.raw_pdf_position_counts,
            )
            logger.info(
                "Job %s: Reconciled — master_set=%d positions (%d synthetic MISSING)",
                job_id,
                transform_result.expected_position_count,
                sum(1 for r in transform_result.rows if r.is_synthetic),
            )

            # 4. Cross-validate
            stage = "cross-validation"
            cv_result = cross_validate(transform_result)

            # 5. Score
            stage = "scoring"
            logger.info("Job %s: Scoring", job_id)
            config = load_scoring_config()
            audit = await score_bom_async(
                transform_result,
                mapping,
                cv_result=cv_result,
                schema=schema,
                config=config,
                mapping_validation=mapping_validation,
                counter_check_service=counter_check_service,
                job_id=job_id,
                pdf_path=job.filepath,
            )
            job_store.update(job_id, progress=0.9)

            # 6. Export
            stage = "export"
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            export_path = EXPORT_DIR / f"{job_id}.xlsx"
            export_to_excel(
                audit,
                export_path,
                schema=schema,
                colour_cells=True,
                add_audit_sheet=True,
                meta={"customer": audit.customer},
            )

            job_store.update(
                job_id,
                status="completed",
                progress=1.0,
                audit=audit,
                export_path=export_path,
                customer=audit.customer or "",
            )
            logger.info(
                "Job %s: Completed — GREEN %d, YELLOW %d, RED %d, NEUTRAL %d",
                job_id,
                audit.green_count,
                audit.yellow_count,
                audit.red_count,
                audit.neutral_count,
            )

            # Monitoring-minimum: one structured, machine-parseable metrics line per
            # job (duration, traffic-light split, completeness verdict, guard basis).
            synthetic_missing = sum(1 for r in transform_result.rows if r.is_synthetic)
            logger.info(
                "JOB_METRICS %s",
                json.dumps(
                    {
                        "job_id": job_id,
                        "customer": audit.customer,
                        "duration_s": round(time.perf_counter() - t_start, 2),
                        "pages": bom.source.pages,
                        "extraction_method": (
                            bom.source.extraction_method.value
                            if bom.source.extraction_method
                            else None
                        ),
                        "has_text_layer": bool(bom.metadata.get("has_text_layer")),
                        "guard_basis": audit.guard_basis,
                        "completeness_guaranteed": audit.completeness_guaranteed,
                        "expected_position_count": audit.expected_position_count,
                        "synthetic_missing": synthetic_missing,
                        "green": audit.green_count,
                        "yellow": audit.yellow_count,
                        "red": audit.red_count,
                        "neutral": audit.neutral_count,
                        "total_scored": audit.total_scored,
                    },
                    ensure_ascii=False,
                ),
            )

        timeout = _job_timeout_seconds()
        try:
            await asyncio.wait_for(_run_inner(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                "Job %s: timed out after %.0fs during stage '%s'",
                job_id,
                timeout,
                stage,
            )
            _mark_job_failed(
                job_id,
                stage,
                f"Zeitlimit von {timeout:.0f}s überschritten (JOB_TIMEOUT_SECONDS)",
            )

    except Exception as e:  # noqa: BLE001
        logger.exception("Job %s failed during %s: %s", job_id, stage, e)
        _mark_job_failed(job_id, stage, str(e))
    finally:
        if counter_check_service is not None:
            try:
                counter_check_service.release_job(job_id)
                counter_check_service.close()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Job %s: failed to clean up counter-check service", job_id
                )
