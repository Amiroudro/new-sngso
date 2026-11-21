"""Canonical, staging-only edition artifact integrity checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json_bytes, sha256_fingerprint
from .contracts import ContractValidationError, validate_payload


class EditionArtifactIntegrityError(ValueError):
    """Raised when an edition payload and its provenance manifest disagree."""


@dataclass(frozen=True, slots=True)
class VerifiedEditionArtifact:
    """Immutable byte representation of a verified artifact pair."""

    payload_bytes: bytes
    manifest_bytes: bytes
    payload_sha256: str
    edition_id: str
    edition_revision: int


def verify_edition_artifact(
    payload: dict[str, Any], manifest: dict[str, Any]
) -> VerifiedEditionArtifact:
    """Validate manifest structure, canonical payload hash, and edition identity.

    This function performs no registry reads, filesystem writes, publication,
    or permission authorization. It verifies only the supplied in-memory pair.
    """
    if not isinstance(payload, dict):
        raise EditionArtifactIntegrityError("edition payload must be an object")
    if not isinstance(manifest, dict):
        raise EditionArtifactIntegrityError("edition manifest must be an object")
    try:
        validate_payload("edition-artifact-manifest", manifest)
    except (ContractValidationError, FileNotFoundError, ValueError) as exc:
        raise EditionArtifactIntegrityError(f"invalid edition manifest: {exc}") from exc

    required_payload_fields = ("edition_id", "edition_revision", "edition_key")
    missing = [field for field in required_payload_fields if field not in payload]
    if missing:
        raise EditionArtifactIntegrityError(
            "edition payload is missing identity fields: " + ", ".join(missing)
        )

    if isinstance(payload["edition_revision"], bool) or not isinstance(
        payload["edition_revision"], int
    ):
        raise EditionArtifactIntegrityError("edition_revision must be an integer")
    if payload["edition_id"] != manifest["edition_id"]:
        raise EditionArtifactIntegrityError("payload and manifest edition IDs differ")
    if payload["edition_revision"] != manifest["edition_revision"]:
        raise EditionArtifactIntegrityError("payload and manifest revisions differ")
    if payload["edition_key"] != manifest["edition_key"]:
        raise EditionArtifactIntegrityError("payload and manifest edition keys differ")

    payload_bytes = canonical_json_bytes(payload)
    payload_hash = sha256_fingerprint(payload)
    if payload_hash != manifest["payload_sha256"]:
        raise EditionArtifactIntegrityError("canonical payload fingerprint does not match manifest")

    return VerifiedEditionArtifact(
        payload_bytes=payload_bytes,
        manifest_bytes=canonical_json_bytes(manifest),
        payload_sha256=payload_hash,
        edition_id=manifest["edition_id"],
        edition_revision=manifest["edition_revision"],
    )
