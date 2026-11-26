"""Portable, no-overwrite export and validation for SQLite audit checkpoints."""
from __future__ import annotations

import hashlib
from typing import Any

from .canonical import canonical_json_bytes
from .sqlite_export import EXPECTED_SCHEMA_VERSION, MANIFEST_FORMAT


CHECKPOINT_FORMAT = "sngso-audit-checkpoint-v1"


class AuditCheckpointError(ValueError):
    """Raised when a checkpoint record is malformed or tampered."""


def _manifest_fingerprint(manifest: dict[str, Any]) -> str:
    body = dict(manifest)
    supplied = body.pop("manifest_sha256", None)
    actual = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if supplied != actual:
        raise AuditCheckpointError("source manifest fingerprint mismatch")
    return actual


def _validate_tip(tip: Any) -> dict[str, Any]:
    if not isinstance(tip, dict) or set(tip) != {"sequence", "event_hash"}:
        raise AuditCheckpointError("audit checkpoint must contain sequence and event_hash")
    sequence = tip.get("sequence")
    event_hash = tip.get("event_hash")
    if (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
        or (sequence == 0 and event_hash is not None)
        or (sequence > 0 and (
            not isinstance(event_hash, str)
            or len(event_hash) != 71
            or not event_hash.startswith("sha256:")
            or any(ch not in "0123456789abcdef" for ch in event_hash[7:])
        ))
    ):
        raise AuditCheckpointError("audit checkpoint values are invalid")
    return {"sequence": sequence, "event_hash": event_hash}


def build_audit_checkpoint_record(
    source_manifest: dict[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Bind a verified manifest's audit tip to its schema and fingerprint."""
    if (
        not isinstance(source_manifest, dict)
        or source_manifest.get("format") != MANIFEST_FORMAT
    ):
        raise AuditCheckpointError("supported SQLite export manifest is required")
    manifest_sha = _manifest_fingerprint(source_manifest)
    source = source_manifest.get("source")
    if (
        not isinstance(source, dict)
        or source.get("schema_version") != EXPECTED_SCHEMA_VERSION
    ):
        raise AuditCheckpointError("source manifest must describe schema version 36")
    tip = _validate_tip(source.get("audit_checkpoint"))
    schema_sha = source.get("schema_sha256")
    if (
        not isinstance(schema_sha, str) or len(schema_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in schema_sha)
    ):
        raise AuditCheckpointError("source schema fingerprint is invalid")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise AuditCheckpointError("generated_at is required")
    record: dict[str, Any] = {
        "format": CHECKPOINT_FORMAT,
        "generated_at": generated_at,
        "source_schema_version": 36,
        "source_schema_sha256": schema_sha,
        "source_manifest_sha256": manifest_sha,
        "audit_checkpoint": tip,
    }
    record["checkpoint_sha256"] = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    load_audit_checkpoint_record(record)
    return record


def load_audit_checkpoint_record(value: dict[str, Any]) -> dict[str, Any]:
    """Validate an exported checkpoint record and return its exact chain tip."""
    if not isinstance(value, dict) or value.get("format") != CHECKPOINT_FORMAT:
        raise AuditCheckpointError("unsupported audit checkpoint record")
    body = dict(value)
    supplied = body.pop("checkpoint_sha256", None)
    actual = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if not isinstance(supplied, str) or supplied != actual:
        raise AuditCheckpointError("audit checkpoint fingerprint mismatch")
    if value.get("source_schema_version") != 36:
        raise AuditCheckpointError("audit checkpoint schema version is invalid")
    schema_sha = value.get("source_schema_sha256")
    manifest_sha = value.get("source_manifest_sha256")
    if (
        not isinstance(schema_sha, str) or len(schema_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in schema_sha)
        or not isinstance(manifest_sha, str) or len(manifest_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in manifest_sha)
    ):
        raise AuditCheckpointError("audit checkpoint source fingerprints are invalid")
    if not isinstance(value.get("generated_at"), str) or not value["generated_at"]:
        raise AuditCheckpointError("audit checkpoint timestamp is invalid")
    return _validate_tip(value.get("audit_checkpoint"))
