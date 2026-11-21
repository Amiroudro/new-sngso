from __future__ import annotations

import copy

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.edition_artifacts import (
    EditionArtifactIntegrityError,
    verify_edition_artifact,
)


def manifest_for(payload: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "manifest_id": "edition-manifest:daily-2026-11-21",
        "edition_id": payload["edition_id"],
        "edition_revision": payload["edition_revision"],
        "edition_key": payload["edition_key"],
        "created_at": "2026-11-21T06:00:00Z",
        "pipeline_run_id": "pipeline-run:daily-2026-11-21",
        "payload_sha256": sha256_fingerprint(payload),
        "story_bindings": [{
            "story_id": "story:sample-001",
            "story_revision": 3,
            "story_snapshot_sha256": "sha256:" + "b" * 64,
            "learning_objects": [
                {"learning_object_id": "learning:sample-bn", "learning_object_revision": 2, "learning_object_sha256": "sha256:" + "c" * 64, "kind": "headline_summary", "language": "bn"},
                {"learning_object_id": "learning:sample-en", "learning_object_revision": 4, "learning_object_sha256": "sha256:" + "d" * 64, "kind": "headline_summary", "language": "en"}
            ]
        }],
        "source_permissions": [{
            "source_id": "source:publisher-a",
            "endpoint_id": "endpoint:publisher-a-news",
            "grant_id": "grant:publisher-a-republication",
            "operation": "republication"
        }]
    }


def payload() -> dict:
    return {
        "edition_id": "edition:daily-2026-11-21",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "stories": [{"story_id": "story:sample-001", "headline": "A reviewed sample"}],
    }


def test_verified_artifact_binds_canonical_payload_and_manifest() -> None:
    candidate = payload()
    manifest = manifest_for(candidate)
    verified = verify_edition_artifact(candidate, manifest)
    assert verified.payload_sha256 == sha256_fingerprint(candidate)
    assert verified.edition_id == candidate["edition_id"]
    assert verified.edition_revision == 1
    assert verified.payload_bytes
    assert verified.manifest_bytes


def test_payload_content_tampering_is_rejected() -> None:
    candidate = payload()
    manifest = manifest_for(candidate)
    candidate["stories"][0]["headline"] = "Changed after manifest creation"
    with pytest.raises(EditionArtifactIntegrityError, match="fingerprint"):
        verify_edition_artifact(candidate, manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("edition_id", "edition:other", "IDs differ"),
        ("edition_revision", 2, "revisions differ"),
        ("edition_key", "2026-11-22", "keys differ"),
        ("edition_revision", True, "must be an integer"),
    ],
)
def test_payload_identity_mismatch_is_rejected(field: str, value, message: str) -> None:
    candidate = payload()
    manifest = manifest_for(candidate)
    candidate[field] = value
    with pytest.raises(EditionArtifactIntegrityError, match=message):
        verify_edition_artifact(candidate, manifest)


def test_manifest_hash_tampering_is_rejected() -> None:
    candidate = payload()
    manifest = manifest_for(candidate)
    manifest["payload_sha256"] = "sha256:" + "f" * 64
    with pytest.raises(EditionArtifactIntegrityError, match="fingerprint"):
        verify_edition_artifact(candidate, manifest)


def test_verified_artifact_bytes_do_not_change_when_input_mutates() -> None:
    candidate = payload()
    manifest = manifest_for(candidate)
    verified = verify_edition_artifact(candidate, manifest)
    original = copy.deepcopy(verified.payload_bytes)
    candidate["stories"][0]["headline"] = "Mutated input"
    assert verified.payload_bytes == original
