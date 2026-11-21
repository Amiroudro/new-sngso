from __future__ import annotations

import pytest

from sngso_engine.edition_artifacts import (
    EditionArtifactIntegrityError,
    verify_pipeline_run_for_artifact,
)
from sngso_engine.registry import SQLiteRegistry


HASH = "sha256:" + "a" * 64


def manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "manifest_id": "edition-manifest:daily-2026-11-21",
        "edition_id": "edition:daily-2026-11-21",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "created_at": "2026-11-21T05:11:00Z",
        "pipeline_run_id": "pipeline-run:daily-2026-11-21",
        "payload_sha256": HASH,
        "story_bindings": [{
            "story_id": "story:sample-001",
            "story_revision": 1,
            "story_snapshot_sha256": "sha256:" + "b" * 64,
            "learning_objects": [
                {"learning_object_id": "learning:bn", "learning_object_revision": 1, "learning_object_sha256": "sha256:" + "c" * 64, "kind": "headline_summary", "language": "bn"},
                {"learning_object_id": "learning:en", "learning_object_revision": 1, "learning_object_sha256": "sha256:" + "d" * 64, "kind": "headline_summary", "language": "en"}
            ]
        }],
        "source_permissions": [{
            "source_id": "source:publisher-a",
            "endpoint_id": "endpoint:publisher-a-news",
            "grant_id": "grant:publisher-a-republication",
            "operation": "republication"
        }]
    }


def running_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "pipeline-run:daily-2026-11-21",
        "status": "running",
        "started_at": "2026-11-21T05:00:00Z",
        "software_version": "2.0.0",
        "config_hash": "sha256:" + "e" * 64,
        "input_hashes": ["sha256:" + "f" * 64],
        "initiated_by": "scheduler:daily",
    }


def succeed(registry: SQLiteRegistry, output_hash: str = HASH) -> None:
    started = running_record()
    registry.add_pipeline_run(started, actor="scheduler", reason="start daily pipeline")
    completed = dict(started)
    completed.update(
        status="succeeded",
        finished_at="2026-11-21T05:10:00Z",
        output_hashes=[output_hash],
    )
    registry.transition_pipeline_run(
        started["id"], completed, expected_revision=1,
        actor="worker", reason="pipeline completed",
    )


def test_manifest_requires_successful_registry_run_with_matching_output() -> None:
    with SQLiteRegistry() as registry:
        succeed(registry)
        result = verify_pipeline_run_for_artifact(registry, manifest(), HASH)
        assert result.pipeline_run_id == "pipeline-run:daily-2026-11-21"
        assert result.revision == 2
        assert result.output_fingerprint == HASH


def test_manifest_rejects_unknown_or_incomplete_pipeline_run() -> None:
    with SQLiteRegistry() as registry:
        with pytest.raises(EditionArtifactIntegrityError, match="unknown pipeline run"):
            verify_pipeline_run_for_artifact(registry, manifest(), HASH)
        registry.add_pipeline_run(running_record(), actor="scheduler", reason="start")
        with pytest.raises(EditionArtifactIntegrityError, match="has not succeeded"):
            verify_pipeline_run_for_artifact(registry, manifest(), HASH)


def test_manifest_rejects_pipeline_output_hash_mismatch() -> None:
    with SQLiteRegistry() as registry:
        succeed(registry, "sha256:" + "9" * 64)
        with pytest.raises(EditionArtifactIntegrityError, match="does not bind"):
            verify_pipeline_run_for_artifact(registry, manifest(), HASH)


def test_manifest_cannot_predate_pipeline_completion() -> None:
    with SQLiteRegistry() as registry:
        succeed(registry)
        candidate = manifest()
        candidate["created_at"] = "2026-11-21T05:09:00Z"
        with pytest.raises(EditionArtifactIntegrityError, match="predates"):
            verify_pipeline_run_for_artifact(registry, candidate, HASH)
