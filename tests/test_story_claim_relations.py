"""Curated claim-to-claim relationship integrity and history tests."""

import json
import sqlite3

import pytest

from sngso_engine import RegistryIntegrityError, SQLiteRegistry, validate_payload

STAMP = "2026-11-20T00:00:00Z"


def _prepared():
    registry = SQLiteRegistry()
    host = "publisher.example.org"
    source_id = "source:publisher"
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
    article_id = "article:one"
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
        ("claim:a", "The authority announced the measure."),
        ("claim:b", "The authority denied announcing the measure."),
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
    return registry


def _relation(*, identifier="claim-relation:one", claim_id="claim:a", related="claim:b"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "story_id": "story:one",
        "claim_id": claim_id,
        "related_claim_id": related,
        "relationship": "contradicts",
        "rationale": "The two propositions conflict as currently worded.",
        "created_by": "editor:one",
        "created_at": STAMP,
        "review_status": "unreviewed",
    }


def test_contradiction_is_symmetric_and_stored_in_canonical_order():
    registry = _prepared()
    try:
        record = registry.add_claim_relation(_relation(claim_id="claim:b", related="claim:a"))
        assert (record.claim_id, record.related_claim_id) == ("claim:a", "claim:b")
        assert registry.story_claim_relations("story:one")[0]["relationship"] == "contradicts"
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_claim_relation_rejects_self_reference():
    registry = _prepared()
    try:
        with pytest.raises(RegistryIntegrityError, match="two different claims"):
            registry.add_claim_relation(_relation(related="claim:a"))
    finally:
        registry.close()


def test_claim_relation_requires_both_claims_in_the_same_story():
    registry = _prepared()
    try:
        registry.add_domain_record("story", {
            "schema_version": "1.0.0",
            "id": "story:other",
            "canonical_title": "Another story",
            "status": "developing",
            "created_at": STAMP,
            "updated_at": STAMP,
            "article_ids": ["article:one"],
        })
        registry.add_domain_record("claim", {
            "schema_version": "1.0.0",
            "id": "claim:other",
            "story_id": "story:other",
            "text": "An unrelated proposition.",
            "language": "en",
            "assessment": "unassessed",
            "provenance": [{
                "source_id": "source:publisher",
                "article_id": "article:one",
                "observed_at": STAMP,
                "method": "human_review",
                "method_version": "1.0",
                "locator": {"uri": "https://publisher.example.org/one"},
            }],
            "created_at": STAMP,
        })
        payload = _relation(identifier="claim-relation:cross-story")
        payload["related_claim_id"] = "claim:other"
        with pytest.raises(RegistryIntegrityError, match="endpoints must belong to its story"):
            registry.add_claim_relation(payload)
    finally:
        registry.close()


def test_claim_relation_correction_preserves_prior_assertion():
    registry = _prepared()
    try:
        registry.add_claim_relation(_relation())
        corrected = _relation(identifier="claim-relation:corrected")
        corrected["relationship"] = "qualifies"
        corrected["created_at"] = "2026-11-20T01:00:00Z"
        corrected["supersedes_relation_id"] = "claim-relation:one"
        corrected["rationale"] = "The second proposition narrows the scope of the first."
        registry.add_claim_relation(corrected)
        history = registry.claim_relation_history("story:one")
        assert len(history) == 2
        assert history[0]["is_superseded"] is True
        assert history[1]["is_superseded"] is False
        assert registry.story_claim_relations("story:one")[0]["relationship"] == "qualifies"
        assert registry.get_claim_relation("claim-relation:one") is not None
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_claim_relation_correction_cannot_branch_and_records_are_append_only():
    registry = _prepared()
    try:
        registry.add_claim_relation(_relation())
        correction = _relation(identifier="claim-relation:correction-a")
        correction["created_at"] = "2026-11-20T01:00:00Z"
        correction["supersedes_relation_id"] = "claim-relation:one"
        registry.add_claim_relation(correction)
        second = _relation(identifier="claim-relation:correction-b")
        second["created_at"] = "2026-11-20T02:00:00Z"
        second["supersedes_relation_id"] = "claim-relation:one"
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            registry.add_claim_relation(second)
        with pytest.raises(sqlite3.IntegrityError, match="claim relations are append-only"):
            registry._connection.execute("DELETE FROM claim_relations WHERE id = 'claim-relation:one'")
    finally:
        registry.close()


def test_reviewed_claim_relation_requires_reviewer_timestamp_and_note():
    registry = _prepared()
    try:
        payload = _relation(identifier="claim-relation:reviewed")
        payload.update({
            "review_status": "accepted",
            "reviewed_at": "2026-11-20T02:00:00Z",
            "reviewed_by": "editor:senior",
            "review_note": "The relationship is supported by the reviewed comparison.",
        })
        record = registry.add_claim_relation(payload)
        assert record.review_status == "accepted"
    finally:
        registry.close()


def test_sqlite_boundary_rejects_self_referential_claim_relation():
    registry = _prepared()
    try:
        payload = _relation(identifier="claim-relation:direct-sql")
        payload["related_claim_id"] = payload["claim_id"]
        with pytest.raises(sqlite3.IntegrityError, match="two different claims"):
            registry._connection.execute(
                """INSERT INTO claim_relations
                (id, story_id, claim_id, related_claim_id, relationship,
                 supersedes_relation_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload["id"], payload["story_id"], payload["claim_id"],
                    payload["related_claim_id"], payload["relationship"], None,
                    STAMP, json.dumps(payload),
                ),
            )
    finally:
        registry.close()


def test_claim_relation_schema_rejects_missing_review_metadata():
    payload = _relation(identifier="claim-relation:bad-review")
    payload["review_status"] = "accepted"
    with pytest.raises(ValueError):
        validate_payload("claim-relation", payload)
