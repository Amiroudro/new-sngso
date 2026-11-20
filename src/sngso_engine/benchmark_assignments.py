"""Pure domain rules for benchmark-review assignment lifecycles.

Persistence, trusted authentication, reviewer qualification verification, and
artifact/review binding remain repository/application responsibilities. These
functions make the state machine deterministic and reject invalid transitions.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any


STATUSES = {"assigned", "accepted", "submitted", "cancelled", "expired"}
_FINGERPRINT_PREFIX = "sha256:"


class BenchmarkAssignmentError(ValueError):
    """An assignment is malformed or a requested transition is not permitted."""


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise BenchmarkAssignmentError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkAssignmentError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BenchmarkAssignmentError(f"{field} must include a timezone")
    return parsed


def validate_benchmark_assignment(assignment: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an assignment record; this does not authenticate its actors."""
    if not isinstance(assignment, Mapping):
        raise TypeError("assignment must be a mapping")
    required = (
        "assignment_id", "artifact_id", "artifact_fingerprint", "language",
        "reviewer_id", "assigned_by", "assigned_at", "due_at", "status", "reason",
    )
    failures = [f"missing or invalid {key}" for key in required
                if not isinstance(assignment.get(key), str) or not assignment.get(key, "").strip()]
    if assignment.get("schema_version") != "1.0.0":
        failures.append("schema_version must be 1.0.0")
    fingerprint = assignment.get("artifact_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 71 or not fingerprint.startswith(_FINGERPRINT_PREFIX) or any(c not in "0123456789abcdef" for c in fingerprint[7:]):
        failures.append("artifact_fingerprint must be a lowercase SHA-256 fingerprint")
    if assignment.get("language") not in {"bn", "en"}:
        failures.append("language must be bn or en")
    if assignment.get("status") not in STATUSES:
        failures.append("status is not supported")
    if assignment.get("reviewer_id") == assignment.get("assigned_by"):
        failures.append("self-assignment is not permitted")

    try:
        assigned_at = _timestamp(assignment.get("assigned_at"), "assigned_at")
        due_at = _timestamp(assignment.get("due_at"), "due_at")
        if due_at <= assigned_at:
            failures.append("due_at must be later than assigned_at")
    except BenchmarkAssignmentError as exc:
        failures.append(str(exc))

    status = assignment.get("status")
    required_by_status = {
        "accepted": ("accepted_at",),
        "submitted": ("accepted_at", "submitted_review_id", "closed_at"),
        "cancelled": ("closed_at", "closure_reason"),
        "expired": ("closed_at", "closure_reason"),
    }
    for key in required_by_status.get(status, ()):
        if not isinstance(assignment.get(key), str) or not assignment.get(key, "").strip():
            failures.append(f"{status} assignments require {key}")
    for key in ("accepted_at", "closed_at"):
        if assignment.get(key) is not None:
            try:
                _timestamp(assignment[key], key)
            except BenchmarkAssignmentError as exc:
                failures.append(str(exc))
    return {"valid": not failures, "failures": failures}


def transition_benchmark_assignment(
    assignment: Mapping[str, Any],
    *,
    event: str,
    actor: str,
    at: str,
    reason: str,
    submitted_review_id: str | None = None,
) -> dict[str, Any]:
    """Return a new assignment state; callers must persist it append-only."""
    report = validate_benchmark_assignment(assignment)
    if not report["valid"]:
        raise BenchmarkAssignmentError("; ".join(report["failures"]))
    if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
        raise BenchmarkAssignmentError("actor and transition reason are required")
    moment = _timestamp(at, "at")
    due = _timestamp(assignment["due_at"], "due_at")
    state = assignment["status"]
    result = dict(assignment)

    if event == "accept":
        if state != "assigned" or actor != assignment["reviewer_id"]:
            raise BenchmarkAssignmentError("only the assigned reviewer may accept an open assignment")
        if moment >= due:
            raise BenchmarkAssignmentError("assignment cannot be accepted at or after its deadline")
        result.update(status="accepted", accepted_at=at)
    elif event == "submit":
        if state != "accepted" or actor != assignment["reviewer_id"]:
            raise BenchmarkAssignmentError("only the assigned reviewer may submit an accepted assignment")
        if moment >= due:
            raise BenchmarkAssignmentError("assignment cannot be submitted at or after its deadline")
        if not isinstance(submitted_review_id, str) or not submitted_review_id.strip():
            raise BenchmarkAssignmentError("submission must reference a review ID")
        result.update(status="submitted", submitted_review_id=submitted_review_id, closed_at=at)
    elif event == "cancel":
        if state not in {"assigned", "accepted"} or actor not in {assignment["reviewer_id"], assignment["assigned_by"]}:
            raise BenchmarkAssignmentError("only the assignee or assigner may cancel an open assignment")
        result.update(status="cancelled", closed_at=at, closure_reason=reason)
    elif event == "expire":
        if state not in {"assigned", "accepted"} or moment < due:
            raise BenchmarkAssignmentError("only an open assignment at or after its deadline may expire")
        if actor != assignment["assigned_by"]:
            raise BenchmarkAssignmentError("only the assigner may record assignment expiry")
        result.update(status="expired", closed_at=at, closure_reason=reason)
    else:
        raise BenchmarkAssignmentError("unsupported assignment event")

    result["last_transition"] = {"event": event, "actor": actor, "at": at, "reason": reason}
    return result
