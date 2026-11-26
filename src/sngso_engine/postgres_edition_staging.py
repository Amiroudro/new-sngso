"""Atomic PostgreSQL staging promotion/rollback and audit service.

The verifier callback is deliberately mandatory. It must re-evaluate the
registry-backed edition, current source permissions, bilingual review state,
and exact export contents using the supplied transaction cursor. Any rows it
relies on must be locked FOR SHARE (or stronger) until this transaction ends.
The service owns the staging-state lock and all durable writes.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .canonical import canonical_json_bytes, sha256_fingerprint
from .edition_artifacts import verify_edition_artifact
from .contracts import validate_payload
from .postgres_audit import append_audit_event
from .postgres_transaction import run_atomic


class PostgresStagingError(RuntimeError):
    """Raised when an edition cannot be safely staged."""


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PostgresStagingError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PostgresStagingError(f"{label} must be a JSON object")
    return value


def _identity_attestation(identity: Any, now: datetime) -> tuple[str, dict[str, Any]]:
    from .benchmark_access import AuthenticatedIdentity

    if not isinstance(identity, AuthenticatedIdentity):
        raise PostgresStagingError("staging requires a trusted authenticated operator identity")
    required = (
        identity.principal_id, identity.issuer, identity.audience, identity.subject,
        identity.session_id, identity.authentication_context, identity.provider_key_id,
        identity.provider_adapter,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required):
        raise PostgresStagingError("staging operator identity attestation is incomplete")
    if (
        identity.authenticated_at.tzinfo is None
        or identity.expires_at.tzinfo is None
        or identity.authenticated_at > now
        or identity.expires_at <= now
        or now - identity.authenticated_at > timedelta(minutes=15)
        or not identity.authentication_methods
    ):
        raise PostgresStagingError("staging requires a valid session and authentication within 15 minutes")
    session_fingerprint = "sha256:" + hashlib.sha256(
        identity.session_id.encode("utf-8")
    ).hexdigest()
    return identity.principal_id, {
        "issuer": identity.issuer,
        "audience": identity.audience,
        "subject": identity.subject,
        "authenticated_at": identity.authenticated_at.isoformat(),
        "expires_at": identity.expires_at.isoformat(),
        "authentication_methods": list(identity.authentication_methods),
        "authentication_context": identity.authentication_context,
        "provider_key_id": identity.provider_key_id,
        "provider_adapter": identity.provider_adapter,
        "session_fingerprint": session_fingerprint,
    }


def _require_human_role(cursor: Any, principal_id: str, role: str) -> None:
    cursor.execute(
        """SELECT roles_json, active, principal_kind
           FROM benchmark_principals
           WHERE principal_id = %s
           FOR SHARE""",
        (principal_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise PostgresStagingError(f"required {role} principal is not registered")
    roles_json, active, principal_kind = row
    try:
        roles = json.loads(roles_json)
    except (TypeError, ValueError) as exc:
        raise PostgresStagingError("registered principal roles are corrupt") from exc
    if active != 1 or principal_kind != "human" or role not in roles:
        raise PostgresStagingError(f"principal must be an active human with role {role}")


def stage_edition_export(
    connection: Any,
    *,
    manifest_id: str,
    operation: str,
    identity: Any,
    reason: str,
    expected_state_revision: int,
    reverify_export: Callable[[Any, dict[str, Any], datetime], dict[str, Any]],
    event_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically reverify, stage, advance state, and append the global audit.

    `reverify_export(cursor, artifact, evaluated_at)` must return the exact
    export package as a JSON object. It executes inside the same transaction
    after the staging-state lock is acquired. The callback is trusted
    application code, not a request-supplied function.
    """
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        raise ValueError("manifest_id is required")
    if operation not in {"promote", "rollback"}:
        raise ValueError("operation must be promote or rollback")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("staging reason is required")
    if isinstance(expected_state_revision, bool) or not isinstance(expected_state_revision, int) or expected_state_revision < 0:
        raise ValueError("expected_state_revision must be a non-negative integer")
    if not callable(reverify_export):
        raise TypeError("a trusted transaction-bound export re-verifier is required")
    if event_id is None:
        event_id = "edition-staging:" + uuid.uuid4().hex
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")

    evaluated_at = now or datetime.now(timezone.utc)
    if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
        raise ValueError("staging evaluation time must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(timezone.utc)
    operator_id, operator_attestation = _identity_attestation(identity, evaluated_at)
    occurred_at = evaluated_at.isoformat().replace("+00:00", "Z")

    def operation_in_transaction(cursor: Any) -> dict[str, Any]:
        # This row lock serializes all staging operations for the channel.
        cursor.execute(
            """SELECT state_revision, current_event_id, current_export_id,
                      lkg_event_id, lkg_export_id
               FROM edition_staging_channel_state
               WHERE channel = 'staging'
               FOR UPDATE"""
        )
        state = cursor.fetchone()
        if state is None:
            raise PostgresStagingError("staging channel state is not provisioned")
        state_revision, current_event_id, current_export_id, lkg_event_id, lkg_export_id = state
        if state_revision != expected_state_revision:
            raise PostgresStagingError("staging channel state revision conflict")

        cursor.execute(
            """SELECT manifest_id, edition_id, edition_revision, payload_sha256,
                      payload_json, manifest_json
               FROM edition_artifacts
               WHERE manifest_id = %s
               FOR SHARE""",
            (manifest_id,),
        )
        artifact_row = cursor.fetchone()
        if artifact_row is None:
            raise PostgresStagingError("unknown immutable edition artifact")
        (
            stored_manifest_id, edition_id, edition_revision, payload_sha256,
            payload_raw, manifest_raw,
        ) = artifact_row
        payload = _json_object(payload_raw, "stored edition payload")
        manifest = _json_object(manifest_raw, "stored edition manifest")
        verified_artifact = verify_edition_artifact(payload, manifest)
        if (
            manifest.get("manifest_id") != stored_manifest_id
            or manifest.get("edition_id") != edition_id
            or manifest.get("edition_revision") != edition_revision
            or verified_artifact.payload_sha256 != payload_sha256
        ):
            raise PostgresStagingError("stored artifact fingerprint or manifest binding failed")
        artifact = {
            "manifest_id": stored_manifest_id,
            "edition_id": edition_id,
            "edition_revision": edition_revision,
            "payload_sha256": payload_sha256,
            "payload": payload,
            "manifest": manifest,
        }

        cursor.execute(
            """SELECT decision_id, decision, principal_id, edition_id,
                      edition_revision, payload_sha256, payload_json
               FROM edition_decisions
               WHERE manifest_id = %s
               FOR SHARE""",
            (manifest_id,),
        )
        approval = cursor.fetchone()
        if approval is None:
            raise PostgresStagingError("staging requires an immutable approval decision")
        (
            decision_id, decision, approver_id, approved_edition_id,
            approved_revision, approved_hash, approval_payload_raw,
        ) = approval
        approval_payload = _json_object(approval_payload_raw, "approval decision payload")
        if (
            decision != "approve" or approved_edition_id != edition_id
            or approved_revision != edition_revision or approved_hash != payload_sha256
        ):
            raise PostgresStagingError("approval does not authorize this exact artifact")

        cursor.execute(
            """SELECT authorization_id, approval_decision_id, approver_principal_id,
                      authorizer_principal_id, edition_id, edition_revision, payload_sha256,
                      payload_json
               FROM edition_release_authorizations
               WHERE manifest_id = %s
               FOR SHARE""",
            (manifest_id,),
        )
        authorization = cursor.fetchone()
        if authorization is None:
            raise PostgresStagingError("staging requires independent release authorization")
        (
            authorization_id, authorization_decision_id, authorization_approver_id,
            authorizer_id, authorized_edition_id, authorized_revision, authorized_hash,
            authorization_payload_raw,
        ) = authorization
        authorization_payload = _json_object(
            authorization_payload_raw, "release authorization payload"
        )
        if (
            authorization_decision_id != decision_id
            or authorization_approver_id != approver_id
            or authorizer_id == approver_id
            or authorized_edition_id != edition_id
            or authorized_revision != edition_revision
            or authorized_hash != payload_sha256
        ):
            raise PostgresStagingError("release authorization does not bind the exact approved artifact")

        _require_human_role(cursor, approver_id, "edition_approver")
        _require_human_role(cursor, authorizer_id, "edition_release_authorizer")
        _require_human_role(cursor, operator_id, "edition_staging_operator")
        if operator_id in {approver_id, authorizer_id}:
            raise PostgresStagingError("staging operator must be independent of approver and authorizer")

        # This callback must query and lock all current edition/story/object,
        # pipeline, source, endpoint, grant and evidence rows it relies on.
        package = reverify_export(cursor, artifact, evaluated_at)
        if not isinstance(package, dict):
            raise PostgresStagingError("export re-verifier must return a JSON object")
        if (
            package.get("manifest_id") != manifest_id
            or package.get("payload_sha256") != payload_sha256
            or package.get("target") != "staging"
            or package.get("publication_authorized") is not False
            or package.get("edition_id") != edition_id
            or package.get("edition_revision") != edition_revision
            or package.get("edition_payload") != payload
            or package.get("provenance_manifest") != manifest
            or package.get("staging_decision") != approval_payload
            or package.get("release_authorization") != authorization_payload
            or not isinstance(package.get("export_id"), str)
            or not package["export_id"].strip()
        ):
            raise PostgresStagingError("fresh export package is not bound to the exact staging artifact")
        try:
            validate_payload("edition-staging-export", package)
        except (ValueError, FileNotFoundError) as exc:
            raise PostgresStagingError(f"fresh staging export contract failed: {exc}") from exc
        export_id = package["export_id"]
        export_sha256 = sha256_fingerprint(package)
        package_json = canonical_json_bytes(package).decode("utf-8")

        if operation == "promote":
            if current_export_id == export_id:
                raise PostgresStagingError("export is already the current staging export")
            cursor.execute(
                """SELECT 1 FROM edition_staging_events
                   WHERE export_id = %s AND operation = 'promote'
                   LIMIT 1""",
                (export_id,),
            )
            if cursor.fetchone() is not None:
                raise PostgresStagingError("an export may be promoted only once")
        else:
            if current_export_id == export_id:
                raise PostgresStagingError("rollback target is already the current staging export")
            cursor.execute(
                """SELECT manifest_id, payload_sha256, export_sha256
                   FROM edition_staging_events
                   WHERE export_id = %s AND operation = 'promote'
                   ORDER BY state_revision DESC
                   LIMIT 1
                   FOR SHARE""",
                (export_id,),
            )
            prior_promotion = cursor.fetchone()
            if prior_promotion is None or prior_promotion != (
                manifest_id, payload_sha256, export_sha256
            ):
                raise PostgresStagingError("rollback target must be an exact previously promoted export")

        next_revision = state_revision + 1
        next_lkg_event_id = current_event_id
        next_lkg_export_id = current_export_id
        event_payload = {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "state_revision": next_revision,
            "channel": "staging",
            "operation": operation,
            "manifest_id": manifest_id,
            "edition_id": edition_id,
            "edition_revision": edition_revision,
            "payload_sha256": payload_sha256,
            "export_id": export_id,
            "export_sha256": export_sha256,
            "previous_current_export_id": current_export_id,
            "lkg_export_id": next_lkg_export_id,
            "operator_principal_id": operator_id,
            "occurred_at": occurred_at,
            "reason": reason.strip(),
            "publication_authorized": False,
            "target": "staging",
            "staging_decision": approval_payload,
            "release_authorization": authorization_payload,
            "operator_identity_attestation": operator_attestation,
        }
        try:
            validate_payload("edition-staging-event", event_payload)
        except (ValueError, FileNotFoundError) as exc:
            raise PostgresStagingError(f"staging event contract failed: {exc}") from exc
        cursor.execute(
            """INSERT INTO edition_staging_events (
                   event_id, state_revision, channel, operation, manifest_id,
                   edition_id, edition_revision, payload_sha256, export_id,
                   export_sha256, previous_current_export_id, lkg_export_id,
                   operator_principal_id, occurred_at, reason,
                   export_package_json, payload_json
               ) VALUES (
                   %s, %s, 'staging', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s, %s
               )""",
            (
                event_id, next_revision, operation, manifest_id, edition_id,
                edition_revision, payload_sha256, export_id, export_sha256,
                current_export_id, next_lkg_export_id, operator_id, occurred_at,
                reason.strip(), package_json,
                canonical_json_bytes(event_payload).decode("utf-8"),
            ),
        )
        cursor.execute(
            """UPDATE edition_staging_channel_state
               SET state_revision = %s, current_event_id = %s,
                   current_export_id = %s, lkg_event_id = %s, lkg_export_id = %s
               WHERE channel = 'staging' AND state_revision = %s""",
            (
                next_revision, event_id, export_id, next_lkg_event_id,
                next_lkg_export_id, state_revision,
            ),
        )
        if getattr(cursor, "rowcount", None) != 1:
            raise PostgresStagingError("staging state compare-and-swap failed")

        audit = append_audit_event(
            cursor,
            event_id="audit:" + event_id,
            event_type=f"edition_staging.{operation}d" if operation == "promote" else "edition_staging.rolled_back",
            entity_type="edition_staging_event",
            entity_id=event_id,
            occurred_at=occurred_at,
            payload=event_payload,
            policy_version="edition-staging-v1",
        )
        return {
            "event": event_payload,
            "export_package": package,
            "export_sha256": export_sha256,
            "state": {
                "channel": "staging",
                "state_revision": next_revision,
                "current_event_id": event_id,
                "current_export_id": export_id,
                "lkg_event_id": next_lkg_event_id,
                "lkg_export_id": next_lkg_export_id,
            },
            "audit": audit,
        }

    return run_atomic(connection, operation_in_transaction)
