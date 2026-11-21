from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.edition_artifacts import (
    EditionArtifactIntegrityError,
    verify_registry_backed_edition_artifact,
)
from sngso_engine.permissions import PermissionDecision
from sngso_engine.registry import SQLiteRegistry


NOW = datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc)
HASH = "sha256:" + "a" * 64


class Record:
    def __init__(self, payload, revision=1):
        self.payload = deepcopy(payload)
        self.revision = revision

    def to_payload(self):
        return deepcopy(self.payload)


class RegistryFixture:
    def __init__(self, pipeline_registry):
        self.pipeline_registry = pipeline_registry
        self.permission_allowed = True
        self.story = {
            "story_id": "story:one",
            "story_revision": 1,
            "story": {"id": "story:one", "status": "ongoing"},
            "articles": [{
                "id": "article:one",
                "record": {
                    "id": "article:one", "source_id": "source:one",
                    "canonical_uri": "https://publisher.example/news/story",
                    "record_status": "active",
                },
            }],
        }
        self.story["fingerprint"] = sha256_fingerprint(self.story)
        self.objects = {
            "learning:bn": Record({
                "id": "learning:bn", "story_id": "story:one",
                "kind": "headline_summary", "language": "bn",
                "status": "approved", "variant_group_id": "variant:one",
                "claim_ids": [], "content": {"text": "বাংলা শিরোনাম"},
            }),
            "learning:en": Record({
                "id": "learning:en", "story_id": "story:one",
                "kind": "headline_summary", "language": "en",
                "status": "approved", "variant_group_id": "variant:one",
                "claim_ids": [], "content": {"text": "English headline"},
            }),
        }
        self.edition = Record({
            "id": "edition:one", "edition_key": "2026-11-21",
            "status": "approved", "content_hash": "",
            "pipeline_run_id": "pipeline-run:one", "story_ids": ["story:one"],
        })
        self.permission_calls = []

    def get_pipeline_run(self, run_id):
        return self.pipeline_registry.get_pipeline_run(run_id)

    def get_domain_record(self, record_type, record_id):
        return self.edition if (record_type, record_id) == ("edition", "edition:one") else None

    def domain_record_history(self, record_type, record_id):
        if record_type == "edition" and record_id == "edition:one":
            return [{"revision": 1, "record": self.edition.to_payload()}]
        if record_type == "story" and record_id == "story:one":
            return [{"revision": 1, "record": {"id": "story:one"}}]
        return []

    def story_editorial_snapshot(self, story_id):
        return deepcopy(self.story)

    def learning_object_history(self, object_id):
        record = self.objects.get(object_id)
        return [{"revision": record.revision, "record": record.to_payload()}] if record else []

    def get_learning_object(self, object_id):
        return self.objects.get(object_id)

    def learning_object_quality_report(self, object_id):
        return {"meets_registry_quality_gates": True, "blockers": []}

    def get_benchmark_principal(self, principal_id):
        return {"principal_id": principal_id, "active": True, "principal_kind": "human",
                "roles": ["benchmark_reviewer"], "languages": ["bn", "en"]}

    def get_source(self, source_id):
        return Record({"id": source_id, "publisher_name": "Example Publisher", "status": "active"})

    def get_endpoint(self, endpoint_id):
        return Record({"id": endpoint_id, "source_id": "source:one",
                       "endpoint_uri": "https://publisher.example/news"})

    def get_permission_grant(self, grant_id):
        return Record({"id": grant_id, "source_id": "source:one"})

    def evaluate_grant(self, grant_id, operation, *, endpoint_id, now):
        self.permission_calls.append((grant_id, operation, endpoint_id, now))
        return PermissionDecision(self.permission_allowed,
                                  "allowed" if self.permission_allowed else "permission_expired",
                                  grant_id, 1)


def _build(registry):
    payload = {
        "edition_id": "edition:one",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "stories": [{"story_id": "story:one"}],
    }
    payload_hash = sha256_fingerprint(payload)
    registry.edition = Record({
        "id": "edition:one", "edition_key": "2026-11-21",
        "status": "approved", "content_hash": payload_hash,
        "pipeline_run_id": "pipeline-run:one", "story_ids": ["story:one"],
    })
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": "edition-manifest:one",
        "edition_id": "edition:one",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "created_at": "2026-11-21T05:30:00Z",
        "pipeline_run_id": "pipeline-run:one",
        "payload_sha256": payload_hash,
        "story_bindings": [{
            "story_id": "story:one",
            "story_revision": 1,
            "story_snapshot_sha256": registry.story["fingerprint"],
            "learning_objects": [
                {"learning_object_id": object_id, "learning_object_revision": 1,
                 "learning_object_sha256": sha256_fingerprint(record.to_payload()),
                 "kind": "headline_summary", "language": record.to_payload()["language"]}
                for object_id, record in registry.objects.items()
            ],
        }],
        "source_permissions": [{
            "source_id": "source:one", "endpoint_id": "endpoint:one",
            "grant_id": "grant:one", "operation": "republication",
        }],
    }
    return payload, manifest


def _succeed_pipeline(registry):
    started = {
        "schema_version": "1.0.0", "id": "pipeline-run:one", "status": "running",
        "started_at": "2026-11-21T05:00:00Z", "software_version": "2.0.0",
        "config_hash": "sha256:" + "b" * 64,
        "input_hashes": ["sha256:" + "c" * 64], "initiated_by": "scheduler:daily",
    }
    registry.add_pipeline_run(started, actor="scheduler", reason="start")
    payload, manifest = _build(RegistryFixture(registry))
    completed = dict(started, status="succeeded", finished_at="2026-11-21T05:10:00Z",
                     output_hashes=[manifest["payload_sha256"]])
    registry.transition_pipeline_run("pipeline-run:one", completed, expected_revision=1,
                                     actor="worker", reason="completed")


def test_registry_backed_verifier_checks_complete_bilingual_staging_bundle(monkeypatch):
    import sngso_engine.language_variants as variants
    monkeypatch.setattr(variants, "verify_independent_bilingual_variants",
                        lambda registry, bn, en: {"publication_authorized": False})
    with SQLiteRegistry() as pipeline_registry:
        _succeed_pipeline(pipeline_registry)
        registry = RegistryFixture(pipeline_registry)
        payload, manifest = _build(registry)
        result = verify_registry_backed_edition_artifact(registry, payload, manifest, now=NOW)
        assert result.story_ids == ("story:one",)
        assert result.learning_object_ids == ("learning:bn", "learning:en")
        assert result.source_permission_checks == 1
        assert result.artifact.payload_sha256 == manifest["payload_sha256"]
        assert result.pipeline_run.pipeline_run_id == "pipeline-run:one"
        assert registry.permission_calls == [("grant:one", "republication", "endpoint:one", NOW)]


def test_registry_backed_verifier_rejects_stale_story_snapshot(monkeypatch):
    import sngso_engine.language_variants as variants
    monkeypatch.setattr(variants, "verify_independent_bilingual_variants",
                        lambda registry, bn, en: {"publication_authorized": False})
    with SQLiteRegistry() as pipeline_registry:
        _succeed_pipeline(pipeline_registry)
        registry = RegistryFixture(pipeline_registry)
        payload, manifest = _build(registry)
        registry.story["story_revision"] = 2
        with pytest.raises(EditionArtifactIntegrityError, match="story revision"):
            verify_registry_backed_edition_artifact(registry, payload, manifest, now=NOW)


def test_registry_backed_verifier_rejects_expired_republication_grant(monkeypatch):
    import sngso_engine.language_variants as variants
    monkeypatch.setattr(variants, "verify_independent_bilingual_variants",
                        lambda registry, bn, en: {"publication_authorized": False})
    with SQLiteRegistry() as pipeline_registry:
        _succeed_pipeline(pipeline_registry)
        registry = RegistryFixture(pipeline_registry)
        registry.permission_allowed = False
        payload, manifest = _build(registry)
        with pytest.raises(EditionArtifactIntegrityError, match="permission denied"):
            verify_registry_backed_edition_artifact(registry, payload, manifest, now=NOW)
