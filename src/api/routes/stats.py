"""Statistics / reporting endpoints.

All aggregates are derived from the cheap Phase-0 summary columns
(job_store.list_summaries — no audit parsing) plus the feedback corrections
log. Archived (soft-deleted) jobs are excluded, matching the dashboard.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from src.api.job_store import Job, job_store
from src.export.feedback_store import FeedbackStore

router = APIRouter(prefix="/stats")

_feedback = FeedbackStore()

# Rough manual-transfer estimate per BOM row (≈ 2 h+ for a typical ~40-row BOM).
# Configurable via the settings page in a later phase.
MINUTES_PER_ROW = 3.0


def _completed() -> list[Job]:
    return [j for j in job_store.list_summaries() if j.status == "completed"]


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


@router.get("/overview")
def overview():
    """Headline KPIs across all (non-archived) jobs."""
    jobs = job_store.list_summaries()
    completed = [j for j in jobs if j.status == "completed"]

    total_cells = sum(j.total_cells for j in completed)
    total_rows = sum(j.total_rows for j in completed)
    green = sum(j.green_count for j in completed)
    yellow = sum(j.yellow_count for j in completed)
    red = sum(j.red_count for j in completed)
    neutral = sum(j.neutral_count for j in completed)
    manual = sum(j.manual_confirmed_count for j in completed)

    automation_rate = _rate(green, total_cells)
    time_saved_hours = round(total_rows * MINUTES_PER_ROW / 60.0 * automation_rate, 1)

    return {
        "total_jobs": len(jobs),
        "completed_jobs": len(completed),
        "total_rows": total_rows,
        "total_cells": total_cells,
        "green": green,
        "yellow": yellow,
        "red": red,
        "neutral": neutral,
        "manual_confirmed": manual,
        "automation_rate": automation_rate,
        "corrections": _feedback.correction_count(),
        "minutes_per_row": MINUTES_PER_ROW,
        "estimated_time_saved_hours": time_saved_hours,
        "status_counts": dict(Counter(j.status for j in jobs)),
    }


@router.get("/timeseries")
def timeseries(bucket: str = Query("month", pattern="^(week|month)$")):
    """Throughput + automation rate per period (week or month)."""
    agg: dict[str, dict[str, int]] = defaultdict(
        lambda: {"jobs": 0, "rows": 0, "green": 0, "cells": 0}
    )
    for j in _completed():
        dt = datetime.fromtimestamp(j.created_at, tz=timezone.utc)
        if bucket == "week":
            iso = dt.isocalendar()
            key = f"{iso[0]}-KW{iso[1]:02d}"
        else:
            key = dt.strftime("%Y-%m")
        a = agg[key]
        a["jobs"] += 1
        a["rows"] += j.total_rows
        a["green"] += j.green_count
        a["cells"] += j.total_cells

    return [
        {
            "period": key,
            "jobs": agg[key]["jobs"],
            "rows": agg[key]["rows"],
            "automation_rate": _rate(agg[key]["green"], agg[key]["cells"]),
        }
        for key in sorted(agg)
    ]


@router.get("/by-customer")
def by_customer():
    """Per-customer throughput, automation rate and correction count."""
    corrections_by_customer = _feedback.stats()
    agg: dict[str, dict[str, int]] = defaultdict(
        lambda: {"jobs": 0, "rows": 0, "green": 0, "yellow": 0, "red": 0, "cells": 0}
    )
    for j in _completed():
        customer = j.customer or "Unbekannt"
        a = agg[customer]
        a["jobs"] += 1
        a["rows"] += j.total_rows
        a["green"] += j.green_count
        a["yellow"] += j.yellow_count
        a["red"] += j.red_count
        a["cells"] += j.total_cells

    rows = [
        {
            "customer": customer,
            "jobs": a["jobs"],
            "rows": a["rows"],
            "green": a["green"],
            "yellow": a["yellow"],
            "red": a["red"],
            "automation_rate": _rate(a["green"], a["cells"]),
            "corrections": corrections_by_customer.get(customer, 0),
        }
        for customer, a in agg.items()
    ]
    rows.sort(key=lambda r: r["jobs"], reverse=True)
    return rows


@router.get("/corrections")
def corrections():
    """Learning signal: how many corrections, by field / customer / month."""
    items = _feedback.load_corrections()
    by_field = Counter(c.target_field for c in items if c.target_field)
    by_customer = Counter((c.customer or "Unbekannt") for c in items)
    by_month = Counter((c.timestamp or "")[:7] for c in items if c.timestamp)

    return {
        "total": len(items),
        "by_field": [{"field": k, "count": v} for k, v in by_field.most_common(10)],
        "by_customer": [
            {"customer": k, "count": v} for k, v in by_customer.most_common()
        ],
        "by_month": [{"period": k, "count": by_month[k]} for k in sorted(by_month) if k],
    }
