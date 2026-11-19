"""Integrity and correction tests for attributed story perspectives."""

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
    article_id = "article:publisher-one"
    registry.add_domain_record("article", {
        "schema_version": "1.0.0",
        "id": article_id,
        "source_id": source_id,
        "canonical_uri": f"https://{host}/news/one",
        "title": "Publisher report",
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
    registry.add_domain_record("claim", {
        "schema_version": "1.0.0",
        "id": "claim:one",
        "story_id": "story:one",
        "text": "The authority issued a notice.",
        "language": "en",
        "assessment": "unassessed",
        "provenance": [{
            "source_id": source_id,
            "article_id": article_id,
            "observed_at": STAMP,
            "method": "human_review",
            "method_version": "1.0",
            "locator": {"uri": f"https://{host}/news/one"},
        }],
        "created_at": STAMP,
    })
    return registry, source_id, article_id


def _perspective(source_id, article_id, *, identifier="perspective:one", kind="publisher_framing"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "story_id": "story:one",
        "kind": kind,
        "attribution": "Publisher's newsroom",
        **({"source_id": source_id, "article_id": article_id} if kind == "publisher_framing" else {}),
        "language": "en",
        "text": "The publisher frames the development as a policy dispute.",
        "claim_ids": ["claim:one"],
        "created_at": STAMP,
        "review_status": "unreviewed",
    }


def test_publisher_framing_requires_registered_source_and_story_article():
    registry, source_id, article_id = _prepared()
    try:
        record = registry.add_perspective(_perspective(source_id, article_id))
        assert record.kind == "publisher_framing"
        assert registry.get_perspective(record.id) == record
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_publisher_framing_rejects_article_from_another_publisher():
    registry, source_id, article_id = _prepared()
    try:
        other_source = "source:other"
        registry.add_source({
            "schema_version": "1.0.0",
            "id": other_source,
            "publisher_name": "Other",
            "publisher_type": "newspaper",
            "canonical_homepage": "https://other.example.org/",
            "domains": ["other.example.org"],
            "status": "active",
            "created_at": STAMP,
            "updated_at": STAMP,
        })
        with pytest.raises(RegistryIntegrityError, match="must belong to its source and story"):
            registry.add_perspective(_perspective(other_source, article_id))
    finally:
        registry.close()


def test_perspective_rejects_claim_from_another_story():
    registry, source_id, article_id = _prepared()
    try:
        payload = _perspective(source_id, article_id, identifier="perspective:bad")
        payload["claim_ids"] = ["claim:missing"]
        with pytest.raises(RegistryIntegrityError, match="claims must belong to its story"):
            registry.add_perspective(payload)
    finally:
        registry.close()


def test_perspective_correction_appends_and_preserves_history():
    registry, source_id, article_id = _prepared()
    try:
        registry.add_perspective(_perspective(source_id, article_id))
        corrected = _perspective(source_id, article_id, identifier="perspective:corrected")
        corrected["created_at"] = "2026-11-20T01:00:00Z"
        corrected["supersedes_perspective_id"] = "perspective:one"
        corrected["created_by"] = "editor:one"
        corrected["rationale"] = "The original framing omitted the authority's stated position."
        registry.add_perspective(corrected)

        history = registry.perspective_history("story:one")
        assert len(history) == 2
        assert history[0]["is_superseded"] is True
        assert history[1]["is_superseded"] is False
        assert history[1]["record"]["rationale"] == corrected["rationale"]
        assert [item["perspective_id"] for item in registry.story_perspectives("story:one")] == [
            "perspective:corrected"
        ]
        assert registry.get_perspective("perspective:one") is not None
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_perspective_corrections_cannot_branch_or_rewrite_records():
    registry, source_id, article_id = _prepared()
    try:
        registry.add_perspective(_perspective(source_id, article_id))
        for identifier in ("perspective:correction-a", "perspective:correction-b"):
            payload = _perspective(source_id, article_id, identifier=identifier)
            payload["created_at"] = "2026-11-20T01:00:00Z"
            payload["supersedes_perspective_id"] = "perspective:one"
            payload["created_by"] = "editor:one"
            payload["rationale"] = "Correcting an incomplete framing."
            if identifier.endswith("a"):
                registry.add_perspective(payload)
            else:
                with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
                    registry.add_perspective(payload)
        with pytest.raises(sqlite3.IntegrityError, match="perspectives are append-only"):
            registry._connection.execute(
                "UPDATE perspectives SET payload_json = '{}' WHERE id = 'perspective:one'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="perspectives are append-only"):
            registry._connection.execute(
                "DELETE FROM perspectives WHERE id = 'perspective:one'"
            )
    finally:
        registry.close()


def test_publisher_framing_contract_requires_source_and_article():
    payload = _perspective("source:publisher", "article:publisher-one")
    payload.pop("source_id")
    payload.pop("article_id")
    with pytest.raises(ValueError):
        validate_payload("perspective", payload)


def test_stakeholder_position_can_be_attributed_without_publisher_source():
    payload = _perspective(None, None, identifier="perspective:stakeholder", kind="stakeholder_position")
    validate_payload("perspective", payload)


def test_sqlite_boundary_rejects_unattributed_publisher_framing():
    registry, _, _ = _prepared()
    try:
        payload = {
            "schema_version": "1.0.0",
            "id": "perspective:direct-sql",
            "story_id": "story:one",
            "kind": "publisher_framing",
            "attribution": "Unverified publisher label",
            "language": "en",
            "text": "A framing inserted without its publisher/article link.",
            "claim_ids": [],
            "created_at": STAMP,
        }
        with pytest.raises(sqlite3.IntegrityError, match="publisher framing requires its source and article"):
            registry._connection.execute(
                """INSERT INTO perspectives
                (id, story_id, source_id, article_id, supersedes_perspective_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("perspective:direct-sql", "story:one", None, None, None, STAMP, json.dumps(payload)),
            )
    finally:
        registry.close()


def test_approved_perspective_requires_reviewer_timestamp_and_note():
    registry, source_id, article_id = _prepared()
    try:
        payload = _perspective(source_id, article_id, identifier="perspective:approved")
        payload.update({
            "review_status": "approved",
            "reviewed_at": "2026-11-20T02:00:00Z",
            "reviewed_by": "editor:senior",
            "review_note": "Attribution and framing were checked against the linked article.",
        })
        record = registry.add_perspective(payload)
        assert record.to_payload()["review_status"] == "approved"
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_unreviewed_perspective_cannot_carry_review_metadata():
    registry, source_id, article_id = _prepared()
    try:
        payload = _perspective(source_id, article_id, identifier="perspective:premature-review")
        payload["reviewed_by"] = "editor:senior"
        payload["reviewed_at"] = "2026-11-20T02:00:00Z"
        payload["review_note"] = "This must be rejected while unreviewed."
        with pytest.raises(RegistryIntegrityError, match="unreviewed perspective cannot contain review metadata"):
            registry.add_perspective(payload)
    finally:
        registry.close()


def test_sqlite_boundary_rejects_approved_perspective_without_review_metadata():
    registry, source_id, article_id = _prepared()
    try:
        payload = _perspective(source_id, article_id, identifier="perspective:direct-approved")
        payload["review_status"] = "approved"
        with pytest.raises(sqlite3.IntegrityError, match="reviewed perspective requires reviewer, timestamp, and note"):
            registry._connection.execute(
                """INSERT INTO perspectives
                (id, story_id, source_id, article_id, supersedes_perspective_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload["id"], payload["story_id"], source_id, article_id, None,
                    STAMP, json.dumps(payload),
                ),
            )
    finally:
        registry.close()


def test_perspective_schema_requires_complete_review_metadata():
    payload = _perspective("source:publisher", "article:publisher-one", identifier="perspective:missing-review")
    payload["review_status"] = "approved"
    with pytest.raises(ValueError):
        validate_payload("perspective", payload)
