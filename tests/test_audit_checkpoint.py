from __future__ import annotations

import hashlib

import pytest

from sngso_engine.audit_checkpoint import (
    AuditCheckpointError,
    build_audit_checkpoint_record,
    load_audit_checkpoint_record,
)
from sngso_engine.canonical import canonical_json_bytes


def _manifest():
    manifest = {
        "format": "sngso-registry-import-manifest-v1",
        "source": {
            "schema_version": 36,
            "schema_sha256": "a" * 64,
            "audit_checkpoint": {
                "sequence": 3,
                "event_hash": "sha256:" + "b" * 64,
            },
        },
        "tables": [],
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        canonical_json_bytes(manifest)
    ).hexdigest()
    return manifest


def test_checkpoint_record_round_trips_exact_chain_tip():
    record = build_audit_checkpoint_record(
        _manifest(), generated_at="2026-11-24T00:00:00Z"
    )

    assert load_audit_checkpoint_record(record) == {
        "sequence": 3,
        "event_hash": "sha256:" + "b" * 64,
    }
    assert len(record["checkpoint_sha256"]) == 64


def test_checkpoint_record_rejects_tampered_tip():
    record = build_audit_checkpoint_record(
        _manifest(), generated_at="2026-11-24T00:00:00Z"
    )
    record["audit_checkpoint"]["sequence"] = 4

    with pytest.raises(AuditCheckpointError, match="fingerprint mismatch"):
        load_audit_checkpoint_record(record)


@pytest.mark.parametrize(
    "tip",
    [
        {"sequence": True, "event_hash": "sha256:" + "b" * 64},
        {"sequence": 0, "event_hash": "sha256:" + "b" * 64},
        {"sequence": 1, "event_hash": None},
        {"sequence": 1, "event_hash": "sha256:" + "g" * 64},
    ],
)
def test_checkpoint_record_rejects_invalid_tips(tip):
    manifest = _manifest()
    manifest["source"]["audit_checkpoint"] = tip
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = hashlib.sha256(
        canonical_json_bytes(manifest)
    ).hexdigest()

    with pytest.raises(AuditCheckpointError, match="audit checkpoint values"):
        build_audit_checkpoint_record(
            manifest, generated_at="2026-11-24T00:00:00Z"
        )


def test_row_dry_run_requires_checkpoint_for_matching_source_schema(tmp_path):
    import json

    from sngso_engine.postgres_row_dry_run import _load_external_checkpoint

    record = build_audit_checkpoint_record(
        _manifest(), generated_at="2026-11-24T00:00:00Z"
    )
    path = tmp_path / "audit-checkpoint.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    assert _load_external_checkpoint(
        path, expected_schema_sha256="a" * 64
    ) == record["audit_checkpoint"]
    with pytest.raises(ValueError, match="different source schema"):
        _load_external_checkpoint(
            path, expected_schema_sha256="c" * 64
        )
