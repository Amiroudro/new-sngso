from __future__ import annotations

import json
from copy import deepcopy

import pytest

from sngso_engine.edition_assembly import (
    EditionAssemblyError,
    assemble_bilingual_edition_artifact,
)


class Record:
    def __init__(self, payload, revision=1):
        self._payload = deepcopy(payload)
        self.revision = revision

    def to_payload(self):
        return deepcopy(self._payload)


class AssemblyRegistry:
    def __init__(self):
        self.edition = Record({
            "id": "edition:assembly",
            "edition_key": "2026-11-21",
            "status": "in_review",
            "pipeline_run_id": "pipeline-run:assembly",
            "story_ids": ["story:one"],
        })
        self.story = {
            "story_id": "story:one",
            "story_revision": 3,
            "fingerprint": "sha256:" + "a" * 64,
            "story": {"id": "story:one", "canonical_title": "A developing story", "status": "developing"},
            "articles": [{
                "id": "article:one",
                "revision": 2,
                "record": {
                    "id": "article:one", "source_id": "source:one",
                    "canonical_uri": "https://publisher.example/news/story",
                    "title": "Original publisher headline",
                    "source_language": "en", "record_status": "active",
                    "published_at": "2026-11-21T04:00:00Z",
                },
            }],
        }
        self.objects = {
            "learning:bn": Record({
                "id": "learning:bn", "story_id": "story:one",
                "kind": "headline_summary", "language": "bn", "status": "approved",
                "variant_group_id": "variant:one", "claim_ids": ["claim:one"],
                "content": {"text": "বাংলা শিরোনাম", "segments": [
                    {"text": "বাংলা শিরোনাম", "claim_ids": ["claim:one"], "role": "fact"}
                ]},
            }, 2),
            "learning:en": Record({
                "id": "learning:en", "story_id": "story:one",
                "kind": "headline_summary", "language": "en", "status": "approved",
                "variant_group_id": "variant:one", "claim_ids": ["claim:one"],
                "content": {"text": "English headline", "segments": [
                    {"text": "English headline", "claim_ids": ["claim:one"], "role": "fact"}
                ]},
            }, 4),
        }

    def get_domain_record(self, kind, identifier):
        return self.edition if (kind, identifier) == ("edition", "edition:assembly") else None

    def domain_record_history(self, kind, identifier):
        if (kind, identifier) == ("edition", "edition:assembly"):
            return [{"revision": 1, "record": self.edition.to_payload()}]
        return []

    def story_editorial_snapshot(self, story_id):
        return deepcopy(self.story)

    def get_learning_object(self, object_id):
        return self.objects.get(object_id)

    def learning_object_history(self, object_id):
        record = self.objects.get(object_id)
        return [{"revision": record.revision, "record": record.to_payload()}] if record else []

    def learning_object_quality_report(self, object_id):
        return {"meets_registry_quality_gates": True, "blockers": []}

    def get_source(self, source_id):
        if source_id != "source:one":
            return None
        return Record({"id": source_id, "publisher_name": "Example Publisher", "status": "active"})


def _assemble(monkeypatch, registry=None, selected=None, permissions=None):
    import sngso_engine.edition_assembly as assembly
    monkeypatch.setattr(
        assembly, "verify_independent_bilingual_variants",
        lambda registry, bn, en: {"publication_authorized": False},
    )
    monkeypatch.setattr(
        assembly, "verify_pipeline_run_for_artifact",
        lambda registry, manifest, digest: object(),
    )
    return assembly.assemble_bilingual_edition_artifact(
        registry or AssemblyRegistry(),
        "edition:assembly",
        selected or {"story:one": ["learning:bn", "learning:en"]},
        permissions or [{
            "source_id": "source:one", "endpoint_id": "endpoint:one",
            "grant_id": "grant:one", "operation": "republication",
        }],
        manifest_id="edition-manifest:assembly",
        created_at="2026-11-21T06:00:00Z",
    )


def test_assembly_preserves_exact_approved_bilingual_content_and_provenance(monkeypatch):
    registry = AssemblyRegistry()
    assembled = _assemble(monkeypatch, registry)
    payload = json.loads(assembled.artifact.payload_bytes)
    manifest = json.loads(assembled.artifact.manifest_bytes)
    story = payload["stories"][0]
    assert story["story_id"] == "story:one"
    assert story["story_revision"] == 3
    assert story["sources"][0]["article_revision"] == 2
    objects = {item["language"]: item for item in story["learning_objects"]}
    assert objects["bn"]["content"]["text"] == "বাংলা শিরোনাম"
    assert objects["en"]["content"]["text"] == "English headline"
    assert objects["bn"]["claim_ids"] == objects["en"]["claim_ids"] == ["claim:one"]
    assert manifest["story_bindings"][0]["story_snapshot_sha256"] == registry.story["fingerprint"]
    assert assembled.story_ids == ("story:one",)
    assert assembled.learning_object_ids == ("learning:bn", "learning:en")
    assert assembled.payload_sha256 == manifest["payload_sha256"]


def test_assembly_rejects_incomplete_language_pair(monkeypatch):
    with pytest.raises(EditionAssemblyError, match="incomplete BN/EN pair"):
        _assemble(monkeypatch, selected={"story:one": ["learning:bn"]})


def test_assembly_rejects_unapproved_learning_object(monkeypatch):
    registry = AssemblyRegistry()
    payload = registry.objects["learning:en"].to_payload()
    payload["status"] = "draft"
    registry.objects["learning:en"] = Record(payload, 4)
    with pytest.raises(EditionAssemblyError, match="not approved"):
        _assemble(monkeypatch, registry)


def test_assembly_rejects_stale_learning_object_history(monkeypatch):
    registry = AssemblyRegistry()
    registry.objects["learning:bn"].revision = 5
    original_history = registry.learning_object_history
    registry.learning_object_history = lambda object_id: (
        [{"revision": 4, "record": registry.objects[object_id].to_payload()}]
        if object_id == "learning:bn" else original_history(object_id)
    )
    with pytest.raises(EditionAssemblyError, match="revision history mismatch"):
        _assemble(monkeypatch, registry)


def test_assembly_requires_explicit_selection_for_every_story(monkeypatch):
    registry = AssemblyRegistry()
    registry.edition = Record({
        "id": "edition:assembly", "edition_key": "2026-11-21",
        "status": "in_review", "pipeline_run_id": "pipeline-run:assembly",
        "story_ids": ["story:one", "story:two"],
    })
    with pytest.raises(EditionAssemblyError, match="cover every edition story"):
        _assemble(monkeypatch, registry)


def test_assembly_rejects_missing_source_article(monkeypatch):
    registry = AssemblyRegistry()
    registry.story["articles"] = []
    with pytest.raises(EditionAssemblyError, match="no active source articles"):
        _assemble(monkeypatch, registry)


def test_assembly_rejects_timezone_naive_manifest_time(monkeypatch):
    import sngso_engine.edition_assembly as assembly
    with pytest.raises(EditionAssemblyError, match="include a timezone"):
        assembly.assemble_bilingual_edition_artifact(
            AssemblyRegistry(), "edition:assembly",
            {"story:one": ["learning:bn", "learning:en"]},
            [{"source_id": "source:one", "endpoint_id": "endpoint:one",
              "grant_id": "grant:one", "operation": "republication"}],
            manifest_id="edition-manifest:assembly",
            created_at="2026-11-21T06:00:00",
        )
