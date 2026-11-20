from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.flutter_compat import (
    FlutterCompatibilityError,
    verify_flutter_staging_candidate,
)
from sngso_engine.permissions import PermissionDecision


NOW = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
SOURCE = {
    "id": "source:one",
    "publication": "Example Publisher",
    "headline": "Registered headline",
    "date": "2026-11-20",
    "url": "https://publisher.example/story",
}


class FakeRecord:
    def __init__(self, payload, revision=1):
        self._payload = deepcopy(payload)
        self.revision = revision

    def to_payload(self):
        return deepcopy(self._payload)


class FakeRegistry:
    def __init__(self, *, permission_allowed=True, summary_revision=1):
        self.permission_allowed = permission_allowed
        self.endpoint_source_id = "source:one"
        self.grant_source_id = "source:one"
        self.principal_languages = ["en"]
        self.records = {
            "learning:headline": FakeRecord({
                "id": "learning:headline", "story_id": "story:one",
                "kind": "headline_summary", "language": "en",
                "status": "approved", "content": {"text": "Approved headline"},
                "language_review": {
                    "reviewer_id": "reviewer:one",
                    "identity_attestation": {"principal_id": "reviewer:one"},
                },
            }),
            "learning:summary": FakeRecord({
                "id": "learning:summary", "story_id": "story:one",
                "kind": "brief_summary", "language": "en",
                "status": "approved", "content": {"text": "Approved summary"},
                "language_review": {
                    "reviewer_id": "reviewer:one",
                    "identity_attestation": {"principal_id": "reviewer:one"},
                },
            }, revision=summary_revision),
        }
        self.permission_calls = []

    def get_domain_record(self, record_type, record_id):
        if record_type != "edition" or record_id != "edition:staging-one":
            return None
        return FakeRecord({
            "id": record_id, "edition_key": "2026-11-20", "status": "approved",
            "story_ids": ["story:one"],
            "language_variants": [{"language": "en", "status": "approved"}],
        })

    def domain_record_history(self, record_type, record_id):
        return [{"revision": 1}] if record_type == "edition" and record_id == "edition:staging-one" else []

    def story_editorial_snapshot(self, story_id):
        return {
            "story_id": story_id,
            "story": {"id": story_id, "status": "ongoing", "topic_tags": []},
            "articles": [{
                "id": "article:one",
                "record": {
                    "id": "article:one", "source_id": "source:one",
                    "title": SOURCE["headline"], "canonical_uri": SOURCE["url"],
                    "published_at": "2026-11-20T08:00:00Z",
                    "record_status": "active",
                },
            }],
        }

    def get_source(self, source_id):
        return {"id": source_id, "publisher_name": "Example Publisher"}

    def get_learning_object(self, object_id):
        return self.records.get(object_id)

    def learning_object_quality_report(self, object_id):
        return {"meets_registry_quality_gates": True, "blockers": []}

    def get_benchmark_principal(self, principal_id):
        return {
            "principal_id": principal_id, "active": True,
            "principal_kind": "human", "languages": self.principal_languages,
        }

    def get_endpoint(self, endpoint_id):
        if endpoint_id != "endpoint:one":
            return None
        return FakeRecord({
            "id": endpoint_id, "source_id": self.endpoint_source_id,
            "endpoint_uri": "https://publisher.example/",
        })

    def get_permission_grant(self, grant_id):
        if grant_id != "grant:republication":
            return None
        return FakeRecord({"id": grant_id, "source_id": self.grant_source_id})

    def evaluate_grant(self, grant_id, operation, *, endpoint_id, now):
        self.permission_calls.append((grant_id, operation, endpoint_id, now))
        return PermissionDecision(
            self.permission_allowed,
            "allowed" if self.permission_allowed else "permission_expired",
            grant_id,
            1,
        )


def _candidate(registry=None):
    registry = registry or FakeRegistry()
    story = {
        "id": "story:one",
        "headline": "Approved headline",
        "category": "",
        "summary": "Approved summary",
        "sources": [deepcopy(SOURCE)],
    }
    client = {
        "edition_date": "2026-11-20",
        "edition_day": "Friday",
        "coverage": "",
        "status": "staging",
        "counts": {"stories": 1, "words": 0, "phrases": 0, "mcqs": 0},
        "stories": [story],
        "words": [],
        "revision_words": [],
        "phrases": [],
        "mcqs": [],
        "sources": [deepcopy(SOURCE)],
    }
    manifest = {
        "schema_version": "1.0.0",
        "target_contract": "flutter-edition-v1",
        "edition_id": "edition:staging-one",
        "edition_revision": 1,
        "edition_date": "2026-11-20",
        "payload_sha256": sha256_fingerprint(client),
        "story_bindings": [{
            "story_id": "story:one",
            "fields": {
                "headline": {"learning_object_id": "learning:headline", "revision": 1},
                "summary": {"learning_object_id": "learning:summary", "revision": 1},
            },
        }],
        "source_permissions": [{
            "source_id": "source:one", "endpoint_id": "endpoint:one",
            "grant_id": "grant:republication", "operation": "republication",
        }],
    }
    return registry, client, manifest


def test_staging_candidate_verifies_exact_records_and_current_permission():
    registry, client, manifest = _candidate()
    result = verify_flutter_staging_candidate(registry, client, manifest, now=NOW)
    assert result["status"] == "verified_for_staging_only"
    assert result["publication_authorized"] is False
    assert result["story_ids"] == ["story:one"]
    assert result["learning_object_ids"] == ["learning:headline", "learning:summary"]
    assert result["source_ids"] == ["source:one"]
    assert registry.permission_calls == [
        ("grant:republication", "republication", "endpoint:one", NOW),
    ]


def test_staging_candidate_rejects_published_status_and_payload_tampering():
    registry, client, manifest = _candidate()
    client["status"] = "published"
    manifest["payload_sha256"] = sha256_fingerprint(client)
    with pytest.raises(FlutterCompatibilityError, match="draft or staging"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)

    registry, client, manifest = _candidate()
    client["stories"][0]["summary"] = "Changed after review"
    manifest["payload_sha256"] = sha256_fingerprint(client)
    with pytest.raises(FlutterCompatibilityError, match="does not match its approved learning object"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)


def test_staging_candidate_rejects_stale_object_revision_and_denied_permission():
    registry, client, manifest = _candidate(FakeRegistry(summary_revision=2))
    with pytest.raises(FlutterCompatibilityError, match="missing or stale"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)

    registry, client, manifest = _candidate(FakeRegistry(permission_allowed=False))
    with pytest.raises(FlutterCompatibilityError, match="permission denied"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)


def test_staging_candidate_rejects_unlinked_or_mismatched_source_and_unsupported_content():
    registry, client, manifest = _candidate()
    client["stories"][0]["sources"][0]["url"] = "https://attacker.example/"
    manifest["payload_sha256"] = sha256_fingerprint(client)
    with pytest.raises(FlutterCompatibilityError, match="headline or URL"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)

    registry, client, manifest = _candidate()
    client["mcqs"] = [{"question": "unbound content"}]
    manifest["payload_sha256"] = sha256_fingerprint(client)
    with pytest.raises(FlutterCompatibilityError, match="must be empty: mcqs"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)


def test_staging_candidate_requires_timezone_aware_permission_recheck_time():
    registry, client, manifest = _candidate()
    with pytest.raises(FlutterCompatibilityError, match="include a timezone"):
        verify_flutter_staging_candidate(
            registry, client, manifest, now=datetime(2026, 11, 20, 14, 0)
        )


def test_staging_candidate_rejects_unbound_coverage_metadata_and_wrong_weekday():
    registry, client, manifest = _candidate()
    client["coverage"] = "invented source window"
    manifest["payload_sha256"] = sha256_fingerprint(client)
    with pytest.raises(FlutterCompatibilityError, match="coverage text is unsupported"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)

    registry, client, manifest = _candidate()
    client["edition_day"] = "Thursday"
    manifest["payload_sha256"] = sha256_fingerprint(client)
    with pytest.raises(FlutterCompatibilityError, match="edition day does not match"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)


def test_staging_candidate_rejects_permission_grant_for_another_publisher():
    registry, client, manifest = _candidate()
    registry.grant_source_id = "source:other"
    with pytest.raises(FlutterCompatibilityError, match="must match the exact source"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)


def test_staging_candidate_requires_current_reviewer_language_qualification():
    registry, client, manifest = _candidate()
    registry.principal_languages = ["bn"]
    with pytest.raises(FlutterCompatibilityError, match="not an active human principal"):
        verify_flutter_staging_candidate(registry, client, manifest, now=NOW)
