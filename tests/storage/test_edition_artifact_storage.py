from __future__ import annotations

import json
import sqlite3

import pytest

from sngso_engine.canonical import canonical_json_bytes, sha256_fingerprint
from sngso_engine.edition_artifacts import (
    VerifiedEditionArtifact,
    VerifiedPipelineRun,
    VerifiedRegistryEdition,
)
from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry
from sngso_engine import edition_artifacts as artifact_module
from sngso_engine import registry as registry_module


def verified_pair():
    payload = {
        "edition_id": "edition:stored-one",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "stories": [{"story_id": "story:one"}],
    }
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": "edition-manifest:stored-one",
        "edition_id": "edition:stored-one",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "created_at": "2026-11-21T05:30:00Z",
        "pipeline_run_id": "pipeline-run:stored-one",
        "payload_sha256": sha256_fingerprint(payload),
        "story_bindings": [{
            "story_id": "story:one",
            "story_revision": 1,
            "story_snapshot_sha256": "sha256:" + "b" * 64,
            "learning_objects": [
                {"learning_object_id": "learning:bn", "learning_object_revision": 1,
                 "learning_object_sha256": "sha256:" + "c" * 64,
                 "kind": "headline_summary", "language": "bn"},
                {"learning_object_id": "learning:en", "learning_object_revision": 1,
                 "learning_object_sha256": "sha256:" + "d" * 64,
                 "kind": "headline_summary", "language": "en"},
            ],
        }],
        "source_permissions": [{
            "source_id": "source:one", "endpoint_id": "endpoint:one",
            "grant_id": "grant:one", "operation": "republication",
        }],
    }
    artifact = VerifiedEditionArtifact(
        payload_bytes=canonical_json_bytes(payload),
        manifest_bytes=canonical_json_bytes(manifest),
        payload_sha256=manifest["payload_sha256"],
        edition_id=manifest["edition_id"],
        edition_revision=1,
    )
    pipeline = VerifiedPipelineRun(
        pipeline_run_id="pipeline-run:stored-one", revision=2,
        finished_at="2026-11-21T05:10:00Z",
        output_fingerprint=manifest["payload_sha256"],
    )
    return VerifiedRegistryEdition(
        artifact=artifact, pipeline_run=pipeline,
        story_ids=("story:one",),
        learning_object_ids=("learning:bn", "learning:en"),
        source_permission_checks=1,
    )


def test_verified_edition_artifact_is_persisted_and_retrieved_immutably(monkeypatch):
    verified = verified_pair()
    monkeypatch.setattr(
        artifact_module, "verify_registry_backed_edition_artifact",
        lambda registry, payload, manifest, now=None: verified,
        raising=False,
    )
    # The registry method imports the verifier from edition_artifacts at call time.
    with SQLiteRegistry() as registry:
        monkeypatch.setattr(
            artifact_module, "verify_registry_backed_edition_artifact",
            lambda registry, payload, manifest, now=None: verified,
            raising=False,
        )
        stored = registry.store_verified_edition_artifact(
            verified, actor="editor:one", reason="retain staging artifact"
        )
        assert stored["integrity_verified"] is True
        assert stored["publication_authorized"] is False
        assert stored["reverify_required_before_release"] is True
        assert stored["payload_sha256"] == verified.artifact.payload_sha256
        assert stored["manifest"] == json.loads(verified.artifact.manifest_bytes)
        assert registry.get_edition_artifact("edition-manifest:missing") is None


def test_edition_artifact_rows_reject_updates_and_deletes(monkeypatch):
    verified = verified_pair()
    monkeypatch.setattr(
        artifact_module, "verify_registry_backed_edition_artifact",
        lambda registry, payload, manifest, now=None: verified,
        raising=False,
    )
    with SQLiteRegistry() as registry:
        registry.store_verified_edition_artifact(
            verified, actor="editor:one", reason="retain staging artifact"
        )
        with pytest.raises(sqlite3.IntegrityError, match="edition artifacts are immutable"):
            registry._connection.execute(
                "UPDATE edition_artifacts SET reason = ? WHERE manifest_id = ?",
                ("tampered", "edition-manifest:stored-one"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="edition artifacts are immutable"):
            registry._connection.execute(
                "DELETE FROM edition_artifacts WHERE manifest_id = ?",
                ("edition-manifest:stored-one",),
            )


def test_edition_artifact_duplicate_manifest_or_revision_is_rejected(monkeypatch):
    verified = verified_pair()
    monkeypatch.setattr(
        artifact_module, "verify_registry_backed_edition_artifact",
        lambda registry, payload, manifest, now=None: verified,
        raising=False,
    )
    with SQLiteRegistry() as registry:
        registry.store_verified_edition_artifact(
            verified, actor="editor:one", reason="first store"
        )
        with pytest.raises(RegistryIntegrityError, match="already exists"):
            registry.store_verified_edition_artifact(
                verified, actor="editor:one", reason="duplicate store"
            )


def test_registry_schema_26_migrates_to_27(tmp_path, monkeypatch):
    database = tmp_path / "edition-artifacts-v26.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 26)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 26
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 27)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 27
        tables = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "edition_artifacts" in tables
