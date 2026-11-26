"""Read-only PostgreSQL source-rights evaluation for staging verification.

Unlike the legacy registry's interactive permission-decision endpoint, this
function writes no audit record. The caller is already inside the staging
transaction; every grant, scope, source, endpoint, and evidence row read here
is locked FOR SHARE until that transaction commits or rolls back.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .contracts import ContractValidationError, validate_payload
from .permissions import OPERATIONS, PermissionDecision, evaluate_permission


def evaluate_postgres_grant_for_staging(
    cursor: Any,
    grant_id: str,
    operation: str,
    *,
    endpoint_id: str,
    now: datetime | None = None,
) -> PermissionDecision:
    """Resolve a current grant and all relational evidence under shared locks."""
    if not isinstance(grant_id, str) or not grant_id.strip():
        return PermissionDecision(False, "invalid_permission_id")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        return PermissionDecision(False, "unknown_operation", grant_id)
    if not isinstance(endpoint_id, str) or not endpoint_id.strip():
        return PermissionDecision(False, "endpoint_scope_mismatch", grant_id)
    evaluation_time = now or datetime.now(timezone.utc)
    if (
        not isinstance(evaluation_time, datetime)
        or evaluation_time.tzinfo is None
        or evaluation_time.utcoffset() is None
    ):
        return PermissionDecision(False, "evaluation_time_must_be_timezone_aware", grant_id)
    evaluation_time = evaluation_time.astimezone(timezone.utc)

    cursor.execute(
        """SELECT source_id, status, effective_from, effective_until,
                  revision, payload_json
           FROM permission_grants
           WHERE id = %s
           FOR SHARE""",
        (grant_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return PermissionDecision(False, "no_permission_record", grant_id)
    source_id, indexed_status, effective_from, effective_until, revision, raw_payload = row
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("grant payload must be an object")
        validate_payload("source-permission-grant", payload)
    except (TypeError, ValueError, ContractValidationError, FileNotFoundError):
        return PermissionDecision(False, "invalid_permission_record", grant_id, revision)

    if (
        payload.get("id") != grant_id
        or payload.get("source_id") != source_id
        or payload.get("status") != indexed_status
        or payload.get("effective_from") != effective_from
        or payload.get("effective_until") != effective_until
    ):
        return PermissionDecision(False, "permission_record_integrity_mismatch", grant_id, revision)

    cursor.execute(
        """SELECT endpoint_id FROM grant_endpoints
           WHERE grant_id = %s AND source_id = %s
           ORDER BY endpoint_id
           FOR SHARE""",
        (grant_id, source_id),
    )
    relational_endpoints = [item[0] for item in cursor.fetchall()]
    if relational_endpoints != sorted(payload.get("endpoint_ids", [])):
        return PermissionDecision(False, "permission_scope_relations_mismatch", grant_id, revision)

    cursor.execute(
        """SELECT evidence_id FROM grant_evidence
           WHERE grant_id = %s AND source_id = %s
           ORDER BY evidence_id
           FOR SHARE""",
        (grant_id, source_id),
    )
    relational_evidence = [item[0] for item in cursor.fetchall()]
    if relational_evidence != sorted(payload.get("evidence_ids", [])):
        return PermissionDecision(False, "permission_scope_relations_mismatch", grant_id, revision)

    expected_operation_evidence = sorted(
        (name, evidence_id)
        for name, permission in payload.get("operations", {}).items()
        for evidence_id in permission.get("evidence_ids", [])
    )
    cursor.execute(
        """SELECT operation, evidence_id FROM grant_operation_evidence
           WHERE grant_id = %s AND source_id = %s
           ORDER BY operation, evidence_id
           FOR SHARE""",
        (grant_id, source_id),
    )
    relational_operation_evidence = cursor.fetchall()
    if relational_operation_evidence != expected_operation_evidence:
        return PermissionDecision(False, "permission_scope_relations_mismatch", grant_id, revision)

    cursor.execute(
        "SELECT status FROM sources WHERE id = %s FOR SHARE",
        (source_id,),
    )
    source = cursor.fetchone()
    if source is None:
        return PermissionDecision(False, "source_record_missing", grant_id, revision)
    if source[0] != "active":
        return PermissionDecision(False, "source_not_active", grant_id, revision)

    cursor.execute(
        """SELECT status FROM source_endpoints
           WHERE id = %s AND source_id = %s
           FOR SHARE""",
        (endpoint_id, source_id),
    )
    endpoint = cursor.fetchone()
    if endpoint is None or endpoint_id not in payload.get("endpoint_ids", []):
        return PermissionDecision(False, "endpoint_scope_mismatch", grant_id, revision)
    if endpoint[0] != "active":
        return PermissionDecision(False, "endpoint_not_active", grant_id, revision)

    evidence_ids = payload.get("evidence_ids", [])
    cursor.execute(
        """SELECT id, review_status FROM permission_evidence
           WHERE source_id = %s AND id = ANY(%s)
           ORDER BY id
           FOR SHARE""",
        (source_id, evidence_ids),
    )
    evidence_rows = cursor.fetchall()
    evidence_statuses = {evidence_id: status for evidence_id, status in evidence_rows}
    if len(evidence_statuses) != len(set(evidence_ids)):
        return PermissionDecision(False, "permission_evidence_missing", grant_id, revision)
    if indexed_status == "approved" and any(
        status != "verified" for status in evidence_statuses.values()
    ):
        return PermissionDecision(False, "permission_evidence_not_verified", grant_id, revision)

    operation_record = payload.get("operations", {}).get(operation, {})
    operation_evidence_ids = operation_record.get("evidence_ids", [])
    if operation_record.get("allowed") is True and any(
        evidence_statuses.get(item) != "verified" for item in operation_evidence_ids
    ):
        return PermissionDecision(False, "operation_evidence_not_verified", grant_id, revision)

    decision = evaluate_permission(
        payload,
        operation,
        source_id=source_id,
        endpoint_id=endpoint_id,
        now=evaluation_time,
    )
    return PermissionDecision(
        decision.allowed, decision.reason, decision.permission_id, revision
    )
