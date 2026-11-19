"""Editorial review gate tests for claim-backed timeline events."""

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
            "locator": {"uri": f"https://{host}/one"},
        }],
        "created_at": STAMP,
    })
    return registry


def _event(identifier="event:one"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "story_id": "story:one",
        "title": "Public notice issued",
        "event_type": "announcement",
        "occurred": {"precision": "date", "start": "2026-11-19"},
        "claim_ids": ["claim:one"],
        "created_at": STAMP,
        "created_by": "editor:one",
        "rationale": "The linked claim records the announcement.",
    }


def test_new_timeline_event_defaults_to_unreviewed():
    registry = _prepared()
    try:
        record = registry.add_timeline_event(_event())
        assert record.review_status == "unreviewed"
        assert registry.story_timeline("story:one")[0]["record"]["review_status"] is None if "review_status" in registry.story_timeline("story:one")[0]["record"] else True
    finally:
        registry.close()


def test_approved_timeline_event_requires_and_records_review_metadata():
    registry = _prepared()
    try:
        payload = _event("event:approved")
        payload.update({
            "review_status": "approved",
            "reviewed_at": "2026-11-20T01:00:00Z",
            "reviewed_by": "editor:senior",
            "review_note": "Event date and claim linkage were checked.",
        })
        record = registry.add_timeline_event(payload)
        assert record.review_status == "approved"
        assert registry.story_timeline("story:one")[0]["record"]["reviewed_by"] == "editor:senior"
    finally:
        registry.close()


def test_approved_timeline_event_without_review_metadata_is_rejected():
    payload = _event("event:missing-review")
    payload["review_status"] = "approved"
    with pytest.raises(ValueError):
        validate_payload("timeline-event", payload)


def test_unreviewed_timeline_event_cannot_carry_review_metadata():
    payload = _event("event:premature-review")
    payload.update({
        "review_status": "unreviewed",
        "reviewed_by": "editor",
        "reviewed_at": "2026-11-20T01:00:00Z",
        "review_note": "Not permitted before review.",
    })
    with pytest.raises(ValueError):
        validate_payload("timeline-event", payload)


def test_timeline_review_cannot_predate_event_creation():
    registry = _prepared()
    try:
        payload = _event("event:early-review")
        payload.update({
            "review_status": "rejected",
            "reviewed_at": "2026-11-19T23:00:00Z",
            "reviewed_by": "editor:senior",
            "review_note": "The event assertion needs correction.",
        })
        with pytest.raises(RegistryIntegrityError, match="review cannot predate its creation"):
            registry.add_timeline_event(payload)
    finally:
        registry.close()


def test_sqlite_boundary_rejects_approved_event_without_review_metadata():
    registry = _prepared()
    try:
        payload = _event("event:direct-sql")
        payload["review_status"] = "approved"
        with pytest.raises(sqlite3.IntegrityError, match="reviewed timeline event requires reviewer, timestamp, and note"):
            registry._connection.execute(
                """INSERT INTO timeline_events
                (id, story_id, occurred_sort_key, precision, supersedes_event_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload["id"], payload["story_id"], "2026-11-19", "date",
                    None, STAMP, json.dumps(payload),
                ),
            )
    finally:
        registry.close()
