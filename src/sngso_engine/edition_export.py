from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .canonical import canonical_json_bytes, sha256_fingerprint
from .edition_artifacts import EditionArtifactIntegrityError, verify_registry_backed_edition_artifact
from .registry import RegistryIntegrityError
from .contracts import validate_payload


class StagingExportError(ValueError):
    """Raised when an artifact is not eligible for a controlled staging export."""


@dataclass(frozen=True, slots=True)
class VerifiedStagingExport:
    """Immutable in-memory package for a downstream staging integration."""

    package_bytes: bytes
    package_sha256: str
    export_id: str
    manifest_id: str
    edition_id: str
    edition_revision: int
    publication_authorized: bool = False


def build_verified_staging_export(
    registry: Any, manifest_id: str, *, now: datetime | None = None
) -> VerifiedStagingExport:
    """Build a read-only staging package after approval, dual authorization and fresh checks.

    The package is an in-memory handoff artifact only. It performs no file writes,
    network requests, frontend updates, live promotion, or publication.
    """
    evaluation_time = now or datetime.now(timezone.utc)
    if not isinstance(evaluation_time, datetime) or evaluation_time.tzinfo is None:
        raise StagingExportError("staging export time must be timezone-aware")
    artifact = registry.get_edition_artifact(manifest_id)
    if artifact is None:
        raise StagingExportError("unknown stored edition artifact")
    approval = registry.get_edition_decision(manifest_id)
    if approval is None or approval.get("decision") != "approve":
        raise StagingExportError("staging export requires an approved edition decision")
    authorization = registry.get_edition_release_authorization(manifest_id)
    if authorization is None or authorization.get("release_authorized") is not True:
        raise StagingExportError("staging export requires a separate release authorization")
    if authorization.get("publication_authorized") is not False:
        raise StagingExportError("staging authorization cannot grant publication permission")

    exact_binding = (
        ("manifest_id", manifest_id),
        ("edition_id", artifact["edition_id"]),
        ("edition_revision", artifact["edition_revision"]),
        ("payload_sha256", artifact["payload_sha256"]),
    )
    for field, expected in exact_binding:
        if approval.get(field) != expected or authorization.get(field) != expected:
            raise StagingExportError(f"approval or release authorization has a mismatched {field}")
    if (
        authorization.get("approval_decision_id") != approval.get("decision_id")
        or authorization.get("approver_principal_id") != approval.get("principal_id")
        or authorization.get("authorizer_principal_id") == authorization.get("approver_principal_id")
    ):
        raise StagingExportError("release authorization does not bind the independent staging approval")

    approver = registry.get_benchmark_principal(approval.get("principal_id", ""))
    authorizer = registry.get_benchmark_principal(authorization.get("authorizer_principal_id", ""))
    if (
        approver is None or not approver["active"]
        or approver.get("principal_kind") != "human"
        or "edition_approver" not in approver["roles"]
    ):
        raise StagingExportError("staging approver is no longer an active human approver")
    if (
        authorizer is None or not authorizer["active"]
        or authorizer.get("principal_kind") != "human"
        or "edition_release_authorizer" not in authorizer["roles"]
    ):
        raise StagingExportError("release authorizer is no longer an active human release authorizer")

    try:
        verified = verify_registry_backed_edition_artifact(
            registry, artifact["payload"], artifact["manifest"], now=evaluation_time
        )
    except (EditionArtifactIntegrityError, RegistryIntegrityError, ValueError) as exc:
        raise StagingExportError(f"fresh whole-edition verification failed: {exc}") from exc
    if verified.artifact.payload_sha256 != artifact["payload_sha256"]:
        raise StagingExportError("fresh verification fingerprint differs from stored artifact")

    package_core = {
        "schema_version": "1.0.0",
        "target": "staging",
        "created_at": evaluation_time.astimezone(timezone.utc).isoformat(),
        "manifest_id": manifest_id,
        "edition_id": artifact["edition_id"],
        "edition_revision": artifact["edition_revision"],
        "payload_sha256": artifact["payload_sha256"],
        "edition_payload": artifact["payload"],
        "provenance_manifest": artifact["manifest"],
        "staging_decision": approval,
        "release_authorization": authorization,
        "publication_authorized": False,
    }
    export_id = "staging-export:" + sha256_fingerprint(package_core).removeprefix("sha256:")
    package = {"export_id": export_id, **package_core}
    try:
        validate_payload("edition-staging-export", package)
    except (ValueError, FileNotFoundError) as exc:
        raise StagingExportError(f"staging export contract validation failed: {exc}") from exc
    package_bytes = canonical_json_bytes(package)
    return VerifiedStagingExport(
        package_bytes=package_bytes,
        package_sha256=sha256_fingerprint(package),
        export_id=export_id,
        manifest_id=manifest_id,
        edition_id=artifact["edition_id"],
        edition_revision=artifact["edition_revision"],
        publication_authorized=False,
    )
