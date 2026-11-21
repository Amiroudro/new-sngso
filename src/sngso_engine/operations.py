"""Read-only operational health summaries for the editorial engine.

This module does not schedule work, emit telemetry, contact external services,
or claim production readiness. It summarizes durable registry state for an
operator or a future health adapter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
import os
import re
import sqlite3
import uuid

from .canonical import sha256_fingerprint


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

def verify_registry_backup(
    backup_path: str | Path, *, expected_schema_version: int | None = None
) -> dict[str, Any]:
    """Verify SQLite integrity, schema version, and the retained audit chain read-only."""
    path = Path(backup_path).expanduser().resolve()
    if not path.is_file():
        raise OperationalReportError("backup file does not exist")
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if integrity != "ok":
            raise OperationalReportError("SQLite backup integrity check failed")
        if expected_schema_version is not None and schema_version != expected_schema_version:
            raise OperationalReportError("backup schema version does not match the expected version")
        rows = connection.execute(
            """SELECT sequence, event_id, event_type, entity_type, entity_id,
                      occurred_at, policy_version, payload_json, previous_hash, event_hash
               FROM audit_events ORDER BY sequence"""
        ).fetchall()
        previous_hash = None
        previous_sequence = 0
        for row in rows:
            if row[0] != previous_sequence + 1:
                raise OperationalReportError("backup audit sequence is not contiguous")
            payload = json.loads(row[7])
            expected_hash = sha256_fingerprint({
                "event_id": row[1], "event_type": row[2], "entity_type": row[3],
                "entity_id": row[4], "occurred_at": row[5], "policy_version": row[6],
                "payload": payload, "previous_hash": previous_hash,
            })
            if row[8] != previous_hash or row[9] != expected_hash:
                raise OperationalReportError("backup audit chain verification failed")
            previous_hash = row[9]
            previous_sequence = row[0]
        checkpoint = {"sequence": previous_sequence, "event_hash": previous_hash}
        return {
            "status": "verified",
            "integrity_check": integrity,
            "schema_version": schema_version,
            "audit_chain_valid": True,
            "audit_checkpoint": checkpoint,
            "size_bytes": path.stat().st_size,
        }
    except sqlite3.Error as exc:
        raise OperationalReportError(f"backup verification failed: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _install_database_copy(
    source_connection: sqlite3.Connection,
    destination_path: str | Path,
    *,
    expected_schema_version: int,
) -> dict[str, Any]:
    destination = Path(destination_path).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError("destination directory must already exist")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    target_connection = None
    try:
        target_connection = sqlite3.connect(temporary)
        source_connection.backup(target_connection)
        target_connection.close()
        target_connection = None
        os.chmod(temporary, 0o600)
        verification = verify_registry_backup(
            temporary, expected_schema_version=expected_schema_version
        )
        # A same-directory hard link installs atomically and refuses to overwrite.
        os.link(temporary, destination)
        os.chmod(destination, 0o600)
        return {
            "destination": str(destination),
            "status": "verified",
            "schema_version": verification["schema_version"],
            "size_bytes": verification["size_bytes"],
            "audit_checkpoint": verification["audit_checkpoint"],
            "audit_chain_valid": verification["audit_chain_valid"],
        }
    finally:
        if target_connection is not None:
            target_connection.close()
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def backup_registry_database(registry: Any, destination_path: str | Path) -> dict[str, Any]:
    """Create a consistent, non-overwriting SQLite backup and verify it before install."""
    if not hasattr(registry, "_connection") or not hasattr(registry, "_lock"):
        raise OperationalReportError("backup requires a SQLiteRegistry-compatible adapter")
    with registry._lock:
        if registry._connection.in_transaction:
            raise OperationalReportError("backup cannot start inside an active registry transaction")
        expected_schema_version = registry.schema_version()
        return _install_database_copy(
            registry._connection, destination_path,
            expected_schema_version=expected_schema_version,
        )


def restore_registry_backup(
    backup_path: str | Path, destination_path: str | Path
) -> dict[str, Any]:
    """Restore a verified backup to a new path; never overwrite an existing database."""
    from .registry import _SCHEMA_VERSION

    verify_registry_backup(backup_path, expected_schema_version=_SCHEMA_VERSION)
    source = Path(backup_path).expanduser().resolve()
    source_connection = None
    try:
        source_connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        return _install_database_copy(
            source_connection, destination_path,
            expected_schema_version=_SCHEMA_VERSION,
        )
    except sqlite3.Error as exc:
        raise OperationalReportError(f"backup restore failed: {exc}") from exc
    finally:
        if source_connection is not None:
            source_connection.close()

_TRANSIENT_FAILURE_CODES = frozenset({
    "network_timeout",
    "connection_reset",
    "temporary_dns_failure",
    "http_408",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
    "sqlite_busy",
})
_PERMANENT_FAILURE_CODES = frozenset({
    "permission_denied",
    "permission_expired",
    "permission_revoked",
    "identity_revoked",
    "authentication_failed",
    "contract_validation_failed",
    "schema_invalid",
    "unsupported_operation",
    "source_inactive",
    "endpoint_inactive",
    "content_policy_blocked",
    "integrity_violation",
})


def classify_retry_policy(
    failure_code: str,
    *,
    attempt: int = 1,
    max_attempts: int = 3,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 60.0,
) -> dict[str, Any]:
    """Return a deterministic bounded retry decision; never performs a retry.

    Unknown failure codes fail closed. Permission, identity, contract, policy,
    and integrity failures are permanent and must not be retried automatically.
    """
    import math

    if not isinstance(failure_code, str) or not failure_code.strip():
        raise OperationalReportError("failure_code is required")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise OperationalReportError("attempt must be a positive integer")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise OperationalReportError("max_attempts must be a positive integer")
    if (
        isinstance(base_delay_seconds, bool)
        or not isinstance(base_delay_seconds, (int, float))
        or not math.isfinite(base_delay_seconds)
        or base_delay_seconds <= 0
        or isinstance(max_delay_seconds, bool)
        or not isinstance(max_delay_seconds, (int, float))
        or not math.isfinite(max_delay_seconds)
        or max_delay_seconds <= 0
    ):
        raise OperationalReportError("retry delays must be finite positive numbers")
    if base_delay_seconds > max_delay_seconds:
        raise OperationalReportError("base delay cannot exceed maximum delay")

    code = failure_code.strip().lower()
    if code in _TRANSIENT_FAILURE_CODES:
        category = "transient"
        retryable = attempt < max_attempts
        reason = "transient_retry_allowed" if retryable else "attempt_limit_reached"
    elif code in _PERMANENT_FAILURE_CODES:
        category = "permanent"
        retryable = False
        reason = "permanent_failure_no_retry"
    else:
        category = "unknown"
        retryable = False
        reason = "unknown_failure_code_no_retry"

    delay = 0.0
    if retryable:
        delay = min(float(base_delay_seconds) * (2 ** min(attempt - 1, 30)), float(max_delay_seconds))
    return {
        "failure_code": code,
        "category": category,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "retryable": retryable,
        "delay_seconds": delay,
        "reason": reason,
    }

def pipeline_run_idempotency_key(
    *,
    run_kind: str,
    software_version: str,
    config_hash: str,
    input_hashes: list[str],
    initiated_by: str,
    attempt: int = 1,
) -> str:
    """Build a stable run ID from the logical work request, excluding wall-clock time."""
    import re

    for name, value in (
        ("run_kind", run_kind), ("software_version", software_version),
        ("config_hash", config_hash), ("initiated_by", initiated_by),
    ):
        if not isinstance(value, str) or not value.strip():
            raise OperationalReportError(f"{name} is required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config_hash):
        raise OperationalReportError("config_hash must be a lowercase SHA-256 fingerprint")
    if not isinstance(input_hashes, list):
        raise OperationalReportError("input_hashes must be a list")
    if any(not isinstance(item, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in input_hashes):
        raise OperationalReportError("every input hash must be a lowercase SHA-256 fingerprint")
    if len(input_hashes) != len(set(input_hashes)):
        raise OperationalReportError("input_hashes must be unique")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise OperationalReportError("attempt must be a positive integer")
    digest = sha256_fingerprint({
        "run_kind": run_kind.strip(),
        "software_version": software_version.strip(),
        "config_hash": config_hash,
        "input_hashes": input_hashes,
        "initiated_by": initiated_by.strip(),
        "attempt": attempt,
    })
    return "pipeline-run:" + digest.removeprefix("sha256:")


def create_pipeline_run_idempotently(
    registry: Any,
    payload: dict[str, Any],
    *,
    run_kind: str,
    actor: str,
    reason: str,
    attempt: int = 1,
) -> dict[str, Any]:
    """Create one logical pipeline run once; duplicate requests reuse its durable record.

    A retry attempt is a new logical attempt and therefore receives a distinct
    deterministic run ID. Concurrent duplicate submissions converge on the
    same stored run. This function starts no worker and performs no retry.
    """
    import sqlite3

    if not isinstance(payload, dict):
        raise OperationalReportError("pipeline run payload must be an object")
    if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
        raise OperationalReportError("pipeline run actor and reason are required")
    if payload.get("status") not in {"queued", "running"}:
        raise OperationalReportError("idempotent creation requires a queued or running run")
    expected_id = pipeline_run_idempotency_key(
        run_kind=run_kind,
        software_version=payload.get("software_version"),
        config_hash=payload.get("config_hash"),
        input_hashes=payload.get("input_hashes"),
        initiated_by=payload.get("initiated_by"),
        attempt=attempt,
    )
    supplied_id = payload.get("id")
    if supplied_id not in (None, expected_id):
        raise OperationalReportError("supplied pipeline run ID does not match its idempotency key")
    supplied_attempt = payload.get("attempt", attempt)
    if isinstance(supplied_attempt, bool) or not isinstance(supplied_attempt, int) or supplied_attempt != attempt:
        raise OperationalReportError("payload attempt does not match its idempotency key")
    retry_of = payload.get("retry_of")
    if retry_of is not None and (
        not isinstance(retry_of, str) or not retry_of.strip() or retry_of == expected_id
    ):
        raise OperationalReportError("retry_of must identify a different prior pipeline run")
    candidate = dict(payload, id=expected_id, attempt=attempt)

    def reuse_existing(existing: dict[str, Any]) -> dict[str, Any]:
        record = existing["record"]
        immutable = ("software_version", "config_hash", "input_hashes", "initiated_by", "attempt", "retry_of")
        if any(record.get(field) != candidate.get(field) for field in immutable):
            raise OperationalReportError("idempotency key is bound to different immutable run inputs")
        return {"created": False, "revision": existing["revision"], "record": record}

    existing = registry.get_pipeline_run(expected_id)
    if existing is not None:
        return reuse_existing(existing)
    try:
        created = registry.add_pipeline_run(candidate, actor=actor, reason=reason)
        return {"created": True, "revision": created["revision"], "record": created["record"]}
    except sqlite3.IntegrityError:
        # A competing connection may have inserted the same deterministic ID.
        existing = registry.get_pipeline_run(expected_id)
        if existing is None:
            raise
        return reuse_existing(existing)

def write_external_audit_checkpoint(registry: Any, destination_path: str | Path) -> dict[str, Any]:
    """Write the current verified audit tip to a new owner-only JSON file.

    The operator must copy this file to independently controlled storage;
    writing it beside the database alone does not protect against joint tampering.
    """
    from datetime import datetime, timezone

    destination = Path(destination_path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise FileNotFoundError("checkpoint destination directory must already exist")
    with registry._lock:
        if not registry.verify_audit_chain():
            raise OperationalReportError("cannot checkpoint an invalid audit chain")
        checkpoint = registry.audit_checkpoint()
        if checkpoint["sequence"] < 1 or not checkpoint["event_hash"]:
            raise OperationalReportError("cannot checkpoint an empty audit chain")
        payload = {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "registry_schema_version": registry.schema_version(),
            "sequence": checkpoint["sequence"],
            "event_hash": checkpoint["event_hash"],
        }
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        descriptor = None
        created = False
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            if created:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
            raise
    return {"status": "written", "path": str(destination), **payload}


def verify_external_audit_checkpoint(registry: Any, checkpoint_path: str | Path) -> dict[str, Any]:
    """Verify that the current retained audit chain still contains an external checkpoint."""
    path = Path(checkpoint_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalReportError(f"cannot read audit checkpoint: {exc}") from exc
    required = {"schema_version", "generated_at", "registry_schema_version", "sequence", "event_hash"}
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema_version") != "1.0.0":
        raise OperationalReportError("audit checkpoint shape is invalid")
    try:
        generated_at = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise OperationalReportError("audit checkpoint timestamp is invalid") from exc
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise OperationalReportError("audit checkpoint timestamp must be timezone-aware")
    sequence = payload["sequence"]
    schema_version = payload["registry_schema_version"]
    event_hash = payload["event_hash"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise OperationalReportError("audit checkpoint sequence is invalid")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise OperationalReportError("audit checkpoint schema version is invalid")
    if not isinstance(event_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", event_hash):
        raise OperationalReportError("audit checkpoint hash is invalid")

    with registry._lock:
        if registry.schema_version() < schema_version:
            raise OperationalReportError("registry schema is older than the audit checkpoint")
        if not registry.verify_audit_chain():
            raise OperationalReportError("current registry audit chain is invalid")
        events = registry.audit_events()
        if len(events) < sequence or events[sequence - 1]["sequence"] != sequence:
            raise OperationalReportError("audit chain has been truncated before the external checkpoint")
        if events[sequence - 1]["event_hash"] != event_hash:
            raise OperationalReportError("audit chain does not match the external checkpoint")
        current = registry.audit_checkpoint()
    return {
        "status": "verified",
        "checkpoint_sequence": sequence,
        "current_sequence": current["sequence"],
        "trailing_events": current["sequence"] - sequence,
        "event_hash": event_hash,
    }

def create_pipeline_retry_run(
    registry: Any,
    failed_run_id: str,
    *,
    run_kind: str,
    previous_attempt: int,
    actor: str,
    reason: str,
    max_attempts: int = 3,
    recovery_confirmed: bool = False,
    permissions_rechecked: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a new queued attempt only after an explicitly retryable failed run.

    This is a deliberate operator action, not an automatic retry. It verifies
    that the prior run ID matches its declared logical attempt and carries
    forward the exact immutable inputs and configuration.
    """
    if not isinstance(failed_run_id, str) or not failed_run_id.strip():
        raise OperationalReportError("failed_run_id is required")
    if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
        raise OperationalReportError("retry actor and reason are required")
    if recovery_confirmed is not True or permissions_rechecked is not True:
        raise OperationalReportError("retry requires explicit recovery and permission recheck attestations")
    if isinstance(previous_attempt, bool) or not isinstance(previous_attempt, int) or previous_attempt < 1:
        raise OperationalReportError("previous_attempt must be a positive integer")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise OperationalReportError("max_attempts must be a positive integer")
    previous = registry.get_pipeline_run(failed_run_id)
    if previous is None:
        raise OperationalReportError("unknown pipeline run")
    record = previous["record"]
    if record["status"] != "failed":
        raise OperationalReportError("only a failed pipeline run can be retried")
    expected_previous_id = pipeline_run_idempotency_key(
        run_kind=run_kind,
        software_version=record["software_version"],
        config_hash=record["config_hash"],
        input_hashes=record["input_hashes"],
        initiated_by=record["initiated_by"],
        attempt=previous_attempt,
    )
    if expected_previous_id != failed_run_id:
        raise OperationalReportError("previous attempt number does not match the failed run identity")
    decision = classify_retry_policy(
        record.get("failure_code", "unspecified"),
        attempt=previous_attempt,
        max_attempts=max_attempts,
    )
    if not decision["retryable"]:
        raise OperationalReportError(f"failed run is not eligible for retry: {decision['reason']}")
    started_at = now or datetime.now(timezone.utc)
    if not isinstance(started_at, datetime) or started_at.tzinfo is None or started_at.utcoffset() is None:
        raise OperationalReportError("retry start time must be timezone-aware")
    payload = {
        "schema_version": "1.0.0",
        "status": "queued",
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
        "software_version": record["software_version"],
        "config_hash": record["config_hash"],
        "input_hashes": list(record["input_hashes"]),
        "initiated_by": record["initiated_by"],
        "attempt": previous_attempt + 1,
        "retry_of": failed_run_id,
    }
    created = create_pipeline_run_idempotently(
        registry,
        payload,
        run_kind=run_kind,
        actor=actor,
        reason=reason.strip() + " (operator attested: permissions rechecked; prior side effects reconciled)",
        attempt=previous_attempt + 1,
    )
    return {
        **created,
        "previous_run_id": failed_run_id,
        "attempt": previous_attempt + 1,
        "retry_decision": decision,
    }

