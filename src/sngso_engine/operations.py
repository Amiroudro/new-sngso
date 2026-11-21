"""Read-only operational health summaries for the editorial engine.

This module does not schedule work, emit telemetry, contact external services,
or claim production readiness. It summarizes durable registry state for an
operator or a future health adapter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class OperationalReportError(ValueError):
    """Raised when operational report parameters are invalid."""


def build_operational_health_report(
    registry: Any,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=30),
    recent_failure_window: timedelta = timedelta(hours=24),
    limit: int = 500,
) -> dict[str, Any]:
    """Summarize pipeline-run state and audit integrity without exposing details.

    Only bounded identifiers, statuses, timestamps and failure codes are
    returned. Failure detail, payloads, credentials and source content are
    deliberately excluded. A clean report is not a production-readiness claim.
    """
    evaluated_at = now or datetime.now(timezone.utc)
    if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise OperationalReportError("health report time must be timezone-aware")
    if not isinstance(stale_after, timedelta) or stale_after <= timedelta(0):
        raise OperationalReportError("stale_after must be a positive duration")
    if not isinstance(recent_failure_window, timedelta) or recent_failure_window <= timedelta(0):
        raise OperationalReportError("recent_failure_window must be a positive duration")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise OperationalReportError("limit must be an integer between 1 and 5000")

    current = evaluated_at.astimezone(timezone.utc)
    runs = registry.list_pipeline_runs(limit=limit)
    counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled", "rejected")}
    stale_runs: list[dict[str, str]] = []
    recent_failures: list[dict[str, str]] = []
    latest_started_at: str | None = None

    for item in runs:
        record = item["record"]
        status = record["status"]
        counts[status] += 1
        started = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if latest_started_at is None or started > datetime.fromisoformat(latest_started_at.replace("Z", "+00:00")):
            latest_started_at = record["started_at"]
        if status in {"queued", "running"} and current - started >= stale_after:
            stale_runs.append({"run_id": record["id"], "status": status, "started_at": record["started_at"]})
        if status in {"failed", "rejected"}:
            finished = datetime.fromisoformat(record["finished_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            if current - recent_failure_window <= finished <= current:
                recent_failures.append({
                    "run_id": record["id"],
                    "status": status,
                    "failure_code": record.get("failure_code", "unspecified"),
                    "finished_at": record["finished_at"],
                })

    audit_valid = bool(registry.verify_audit_chain())
    checkpoint = registry.audit_checkpoint()
    if not audit_valid or stale_runs or recent_failures:
        status = "degraded"
    elif not runs:
        status = "not_observed"
    else:
        status = "healthy"

    warnings = []
    if not audit_valid:
        warnings.append("audit_chain_verification_failed")
    if stale_runs:
        warnings.append("stale_pipeline_runs_present")
    if recent_failures:
        warnings.append("recent_pipeline_failures_present")
    if not runs:
        warnings.append("no_pipeline_runs_observed")
    if len(runs) == limit:
        warnings.append("pipeline_run_sample_may_be_truncated")

    return {
        "schema_version": "1.0.0",
        "generated_at": current.isoformat(),
        "status": status,
        "production_ready": False,
        "audit": {"chain_valid": audit_valid, "checkpoint": checkpoint},
        "pipeline_runs": {
            "sample_count": len(runs),
            "sample_limit": limit,
            "sample_may_be_truncated": len(runs) == limit,
            "counts_by_status": counts,
            "latest_started_at": latest_started_at,
            "stale_active_runs": stale_runs,
            "recent_failures": recent_failures,
        },
        "warnings": warnings,
    }
