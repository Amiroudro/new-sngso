"""Single-snapshot story editorial dossier tests."""

import pytest

from sngso_engine import RegistryIntegrityError, SQLiteRegistry

STAMP = "2026-11-20T00:00:00Z"


def _prepared():
    registry = SQLiteRegistry()
    host = "publisher.example.org"
    source_id = "source:publisher"
    article_id = "article:one"
    registry.add_source({
        "schema_version": "1.0.0",
        "id": source_id,
        "publisher_name": "Publisher",
        "publisher_type": "newspaper",
        "canonical_homepage": f"https://{host}/",
        "domains": [host],
        "status": "active",
        "created_at": STAMP,
        "updated_at": STAMP,
    })
    registry.add_domain_record("article", {
        "schema_version": "1.0.0",
        "id": article_id,
        "source_id": source_id,
        "canonical_uri": f"https://{host}/one",
        "title": "Report",
        "source_language": "en",
        "discovered_at": STAMP,
        "record_status": "active",
    })
    registry.add_domain_record("story", {
        "schema_version": "1.0.0",
        "id": "story:one",
        "canonical_title": "A developing story",
        "status": "developing",
        "created_at": STAMP,
        "updated_at": STAMP,
        "article_ids": [article_id],
    })
    for claim_id, text in [
        ("claim:a", "The authority announced a measure."),
        ("claim:b", "The authority later denied the announcement."),
    ]:
        registry.add_domain_record("claim", {
            "schema_version": "1.0.0",
            "id": claim_id,
            "story_id": "story:one",
            "text": text,
            "language": "en",
            "assessment": "unassessed",
            "provenance": [{
                "source_id": source_id,
                "article_id": article_id,
                "observed_at": STAMP,
                "method": "human_review",
                "method_version": "1.0",
                "locator": {"uri": f"https://{host}/one"},
            }],
            "created_at": STAMP,
        })
    registry.add_domain_record("evidence", {
        "schema_version": "1.0.0",
        "id": "evidence:one",
        "claim_id": "claim:a",
        "source_id": source_id,
        "article_id": article_id,
        "relationship": "supports",
        "locator": {"uri": f"https://{host}/one"},
        "assessed_at": STAMP,
    })
    registry.add_timeline_event({
        "schema_version": "1.0.0",
        "id": "event:one",
        "story_id": "story:one",
        "title": "Announcement reported",
        "event_type": "announcement",
        "occurred": {"precision": "date", "start": "2026-11-19"},
        "claim_ids": ["claim:a"],
        "created_at": STAMP,
        "created_by": "editor",
        "rationale": "Linked claim records the reported announcement.",
    })
    registry.add_perspective({
        "schema_version": "1.0.0",
        "id": "perspective:one",
        "story_id": "story:one",
        "kind": "publisher_framing",
        "attribution": "Publisher newsroom",
        "source_id": source_id,
        "article_id": article_id,
        "language": "en",
        "text": "The publisher frames the issue as a policy dispute.",
        "claim_ids": ["claim:a"],
        "created_at": STAMP,
        "review_status": "unreviewed",
    })
    registry.add_claim_relation({
        "schema_version": "1.0.0",
        "id": "claim-relation:one",
        "story_id": "story:one",
        "claim_id": "claim:a",
        "related_claim_id": "claim:b",
        "relationship": "contradicts",
        "rationale": "The two propositions conflict as currently worded.",
        "created_by": "editor",
        "created_at": STAMP,
        "review_status": "unreviewed",
    })
    registry.add_learning_object({
        "schema_version": "1.0.0",
        "id": "learning:summary-bn",
        "story_id": "story:one",
        "kind": "brief_summary",
        "language": "bn",
        "status": "draft",
        "content": {"text": "কর্তৃপক্ষ একটি বিজ্ঞপ্তি জারি করেছে।"},
        "claim_ids": ["claim:a"],
        "created_at": STAMP,
    }, actor="editor", reason="Fixture learning object.")
    return registry


def test_editorial_snapshot_collects_story_evidence_and_views_consistently():
    registry = _prepared()
    try:
        before = registry._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        first = registry.story_editorial_snapshot("story:one")
        second = registry.story_editorial_snapshot("story:one")
        after = registry._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

        assert first["story_revision"] == 1
        assert len(first["articles"]) == 1
        assert len(first["claims"]) == 2
        assert len(first["evidence"]) == 1
        assert len(first["timeline"]) == 1
        assert len(first["perspectives"]) == 1
        assert len(first["claim_relations"]) == 1
        assert len(first["learning_objects"]) == 1
        assert first["story_decisions"] == []
        assert first["fingerprint"].startswith("sha256:")
        assert first["fingerprint"] == second["fingerprint"]
        assert before == after
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_editorial_snapshot_rejects_unknown_story():
    registry = _prepared()
    try:
        with pytest.raises(RegistryIntegrityError, match="unknown story"):
            registry.story_editorial_snapshot("story:missing")
    finally:
        registry.close()
