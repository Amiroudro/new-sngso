"""Fail-closed, operation-specific source permission evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


OPERATIONS = frozenset(
    {
        "discovery",
        "metadata",
        "excerpt_storage",
        "full_text_storage",
        "translation",
        "image_storage",
        "republication",
    }
)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    reason: str
    permission_id: str | None = None


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def evaluate_permission(
    permission: dict[str, Any] | None,
    operation: str,
    *,
    source_id: str | None = None,
    endpoint_id: str | None = None,
    now: datetime | None = None,
) -> PermissionDecision:
    """Evaluate one operation; every missing or ambiguous condition denies.

    This function expects a permission record already selected for the source.
    Contract validation should also be performed at the repository boundary.
    """
    if permission is None:
        return PermissionDecision(False, "no_permission_record")
    if not isinstance(permission, dict):
        return PermissionDecision(False, "invalid_permission_record")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        return PermissionDecision(False, "unknown_operation")

    permission_id = permission.get("id")
    if not isinstance(permission_id, str) or not permission_id.strip():
        return PermissionDecision(False, "invalid_permission_id")
    if permission.get("status") != "approved":
        return PermissionDecision(False, "permission_not_approved", permission_id)

    # New scoped grants must be evaluated against the exact source and endpoint.
    # Legacy records without these fields remain readable during migration, but
    # a record that declares a scope cannot be evaluated without matching input.
    if "source_id" in permission:
        recorded_source_id = permission.get("source_id")
        if not isinstance(recorded_source_id, str) or not recorded_source_id:
            return PermissionDecision(False, "invalid_source_scope", permission_id)
        if source_id != recorded_source_id:
            return PermissionDecision(False, "source_scope_mismatch", permission_id)

    if "endpoint_ids" in permission:
        endpoint_ids = permission.get("endpoint_ids")
        if (
            not isinstance(endpoint_ids, list)
            or not endpoint_ids
            or any(not isinstance(value, str) or not value for value in endpoint_ids)
        ):
            return PermissionDecision(False, "invalid_endpoint_scope", permission_id)
        if endpoint_id is None or endpoint_id not in endpoint_ids:
            return PermissionDecision(False, "endpoint_scope_mismatch", permission_id)

    if now is not None and not isinstance(now, datetime):
        return PermissionDecision(False, "invalid_evaluation_time", permission_id)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return PermissionDecision(False, "evaluation_time_must_be_timezone_aware", permission_id)
    current = current.astimezone(timezone.utc)

    starts = _parse_aware_datetime(permission.get("effective_from"))
    if starts is None:
        return PermissionDecision(False, "invalid_or_missing_effective_from", permission_id)
    if current < starts:
        return PermissionDecision(False, "permission_not_yet_effective", permission_id)

    ends = _parse_aware_datetime(permission.get("effective_until"))
    if ends is None:
        return PermissionDecision(False, "invalid_or_missing_effective_until", permission_id)
    if ends <= starts:
        return PermissionDecision(False, "invalid_permission_window", permission_id)
    if current >= ends:
        return PermissionDecision(False, "permission_expired", permission_id)

    operations = permission.get("operations")
    if not isinstance(operations, dict):
        return PermissionDecision(False, "operation_permissions_missing", permission_id)
    operation_permission = operations.get(operation)
    if not isinstance(operation_permission, dict):
        return PermissionDecision(False, "operation_permission_missing", permission_id)
    if operation_permission.get("status") != "approved":
        return PermissionDecision(False, "operation_not_approved", permission_id)
    if operation_permission.get("allowed") is not True:
        return PermissionDecision(False, "operation_not_allowed", permission_id)

    if "source_id" in permission:
        evidence_ids = permission.get("evidence_ids")
        operation_evidence_ids = operation_permission.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            return PermissionDecision(False, "permission_evidence_missing", permission_id)
        if not isinstance(operation_evidence_ids, list) or not operation_evidence_ids:
            return PermissionDecision(False, "operation_evidence_missing", permission_id)

    # Conditions have no evaluator yet. Never interpret an unhandled condition
    # as satisfied: require human review until a typed condition engine exists.
    conditions = operation_permission.get("conditions", [])
    if not isinstance(conditions, list):
        return PermissionDecision(False, "invalid_operation_conditions", permission_id)
    if conditions:
        return PermissionDecision(False, "operation_conditions_require_review", permission_id)

    return PermissionDecision(True, "approved", permission_id)
