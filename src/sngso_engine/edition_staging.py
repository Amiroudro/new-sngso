"""Atomic staging-only promotion and rollback for verified edition exports."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from .benchmark_access import AuthenticatedIdentity
from .canonical import sha256_fingerprint
from .edition_export import StagingExportError, build_verified_staging_export
from .registry import RegistryIntegrityError, _json_text


class EditionStagingError(RegistryIntegrityError):
    """Raised when a staging promotion or rollback cannot be safely applied."""


def _verified_operator(registry: Any, identity: AuthenticatedIdentity, package: dict[str, Any], now: datetime) -> None:
    if not isinstance(identity, AuthenticatedIdentity):
        raise EditionStagingError("staging operation requires a trusted authenticated identity")
    if (identity.authenticated_at.tzinfo is None or identity.expires_at.tzinfo is None
        or identity.authenticated_at > now or identity.expires_at <= now
        or now - identity.authenticated_at > timedelta(minutes=15)):
        raise EditionStagingError("staging operator authentication must be valid and no older than 15 minutes")
    required = (identity.principal_id, identity.issuer, identity.audience, identity.subject,
                identity.session_id, identity.authentication_context, identity.provider_key_id,
                identity.provider_adapter)
    if not all(isinstance(value, str) and value.strip() for value in required) or not identity.authentication_methods:
        raise EditionStagingError("staging operator identity attestation is incomplete")
    principal = registry.get_benchmark_principal(identity.principal_id)
    if (principal is None or not principal["active"] or principal.get("principal_kind") != "human"
        or "edition_staging_operator" not in principal["roles"]):
        raise EditionStagingError("operation requires an active human edition_staging_operator")
    decision = package["staging_decision"]
    authorization = package["release_authorization"]
    if identity.principal_id in {decision["principal_id"], authorization["authorizer_principal_id"]}:
        raise EditionStagingError("staging operator must be independent of approver and release authorizer")


def _prepare(registry: Any, manifest_id: str, identity: AuthenticatedIdentity, reason: str, now: datetime) -> tuple[Any, dict[str, Any]]:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("staging operation reason is required")
    try:
        export = build_verified_staging_export(registry, manifest_id, now=now)
        package = json.loads(export.package_bytes)
    except (StagingExportError, ValueError, KeyError, TypeError) as exc:
        raise EditionStagingError(f"fresh staging export verification failed: {exc}") from exc
    if export.publication_authorized is not False or package.get("publication_authorized") is not False or package.get("target") != "staging":
        raise EditionStagingError("staging export cannot authorize publication or target a live destination")
    _verified_operator(registry, identity, package, now)
    if sha256_fingerprint(package) != export.package_sha256:
        raise EditionStagingError("staging export package fingerprint verification failed")
    return export, package


def get_edition_staging_state(registry: Any) -> dict[str, Any]:
    row = registry._connection.execute(
        "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
    ).fetchone()
    if row is None:
        raise EditionStagingError("staging channel state is missing")
    return dict(row)


def list_edition_staging_events(registry: Any) -> list[dict[str, Any]]:
    rows = registry._connection.execute(
        "SELECT payload_json FROM edition_staging_events ORDER BY state_revision"
    ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _apply(registry: Any, *, operation: str, manifest_id: str, identity: AuthenticatedIdentity,
           reason: str, expected_state_revision: int, expected_current_export_id: str | None,
           now: datetime, rollback_export_id: str | None = None) -> dict[str, Any]:
    export, package = _prepare(registry, manifest_id, identity, reason, now)
    if rollback_export_id is not None and export.export_id != rollback_export_id:
        raise EditionStagingError("freshly verified rollback export does not match the previously promoted export")
    with registry._transaction() as connection:
        state = connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        if state is None:
            raise EditionStagingError("staging channel state is missing")
        if (isinstance(expected_state_revision, bool) or not isinstance(expected_state_revision, int)
            or expected_state_revision != state["state_revision"]
            or expected_current_export_id != state["current_export_id"]):
            raise EditionStagingError("staging state changed; refresh the channel state and retry")
        if operation == "promote":
            duplicate = connection.execute(
                "SELECT 1 FROM edition_staging_events WHERE export_id = ? AND operation = 'promote'",
                (export.export_id,),
            ).fetchone()
            if duplicate:
                raise EditionStagingError("this exact staging export has already been promoted")
        else:
            target = connection.execute(
                "SELECT event_id FROM edition_staging_events WHERE export_id = ? AND operation = 'promote' ORDER BY state_revision LIMIT 1",
                (export.export_id,),
            ).fetchone()
            if target is None:
                raise EditionStagingError("rollback target must be an earlier successfully promoted staging export")
            if export.export_id == state["current_export_id"]:
                raise EditionStagingError("rollback target is already the current staging export")
        revision = state["state_revision"] + 1
        event_id = f"edition-staging-event:{uuid.uuid4().hex}"
        occurred_at = now.astimezone(timezone.utc).isoformat()
        previous_current = state["current_export_id"]
        lkg_export_id = previous_current
        event = {
            "schema_version": "1.0.0", "event_id": event_id, "state_revision": revision,
            "channel": "staging", "operation": operation, "manifest_id": manifest_id,
            "edition_id": export.edition_id, "edition_revision": export.edition_revision,
            "payload_sha256": package["payload_sha256"], "export_id": export.export_id,
            "export_sha256": export.package_sha256, "previous_current_export_id": previous_current,
            "lkg_export_id": lkg_export_id, "operator_principal_id": identity.principal_id,
            "occurred_at": occurred_at, "reason": reason.strip(), "target": "staging",
            "publication_authorized": False, "staging_decision": package["staging_decision"],
            "release_authorization": package["release_authorization"],
            "operator_identity_attestation": {
                "issuer": identity.issuer, "audience": identity.audience, "subject": identity.subject,
                "authenticated_at": identity.authenticated_at.isoformat(), "expires_at": identity.expires_at.isoformat(),
                "authentication_methods": list(identity.authentication_methods),
                "authentication_context": identity.authentication_context, "provider_key_id": identity.provider_key_id,
                "provider_adapter": identity.provider_adapter,
                "session_fingerprint": "sha256:" + hashlib.sha256(identity.session_id.encode("utf-8")).hexdigest(),
            },
        }
        connection.execute(
            """INSERT INTO edition_staging_events
            (event_id, state_revision, channel, operation, manifest_id, edition_id, edition_revision,
             payload_sha256, export_id, export_sha256, previous_current_export_id, lkg_export_id,
             operator_principal_id, occurred_at, reason, export_package_json, payload_json)
            VALUES (?, ?, 'staging', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, revision, operation, manifest_id, export.edition_id, export.edition_revision,
             package["payload_sha256"], export.export_id, export.package_sha256, previous_current,
             lkg_export_id, identity.principal_id, occurred_at, reason.strip(),
             export.package_bytes.decode("utf-8"), _json_text(event)),
        )
        connection.execute(
            """UPDATE edition_staging_channel_state
            SET state_revision = ?, current_event_id = ?, current_export_id = ?,
                lkg_event_id = ?, lkg_export_id = ? WHERE channel = 'staging'""",
            (revision, event_id, export.export_id, state["current_event_id"], lkg_export_id),
        )
        registry._append_audit(
            connection, event_type=f"edition_staging.{operation}", entity_type="edition_staging_channel",
            entity_id="staging", payload={"event_id": event_id, "state_revision": revision,
            "manifest_id": manifest_id, "export_id": export.export_id, "export_sha256": export.package_sha256,
            "previous_current_export_id": previous_current, "operator_principal_id": identity.principal_id,
            "publication_authorized": False, "reason": reason.strip()},
        )
    return {"event": event, "state": get_edition_staging_state(registry), "export": export}


def promote_edition_to_staging(registry: Any, manifest_id: str, *, identity: AuthenticatedIdentity,
                               reason: str, expected_state_revision: int,
                               expected_current_export_id: str | None, now: datetime | None = None) -> dict[str, Any]:
    """Atomically promote a freshly verified approved export to the internal staging channel."""
    evaluation_time = now or datetime.now(timezone.utc)
    if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
        raise EditionStagingError("staging operation time must be timezone-aware")
    return _apply(registry, operation="promote", manifest_id=manifest_id, identity=identity,
                  reason=reason, expected_state_revision=expected_state_revision,
                  expected_current_export_id=expected_current_export_id, now=evaluation_time)


def rollback_edition_staging(registry: Any, manifest_id: str, *, target_export_id: str,
                             identity: AuthenticatedIdentity, reason: str,
                             expected_state_revision: int, expected_current_export_id: str,
                             now: datetime | None = None) -> dict[str, Any]:
    """Atomically point staging to an earlier successfully promoted, freshly verified export."""
    evaluation_time = now or datetime.now(timezone.utc)
    if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
        raise EditionStagingError("staging operation time must be timezone-aware")
    return _apply(registry, operation="rollback", manifest_id=manifest_id, identity=identity,
                  reason=reason, expected_state_revision=expected_state_revision,
                  expected_current_export_id=expected_current_export_id, now=evaluation_time,
                  rollback_export_id=target_export_id)
