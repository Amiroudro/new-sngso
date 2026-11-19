"""Controlled glossary persistence and vocabulary-reference tests."""

import json
import sqlite3

import pytest

from sngso_engine import RegistryIntegrityError, SQLiteRegistry, validate_payload

STAMP = "2026-11-19T00:00:00Z"


def _prepared():
    registry = SQLiteRegistry()
    host = "publisher.example.org"
    registry.add_source({
        "schema_version": "1.0.0",
        "id": "source:publisher",
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
        "id": "article:one",
        "source_id": "source:publisher",
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
        "article_ids": ["article:one"],
    })
    registry.add_domain_record("claim", {
        "schema_version": "1.0.0",
        "id": "claim:one",
        "story_id": "story:one",
        "text": "The report discusses public accountability.",
        "language": "en",
        "assessment": "unassessed",
        "provenance": [{
            "source_id": "source:publisher",
            "article_id": "article:one",
            "observed_at": STAMP,
            "method": "human_review",
            "method_version": "1.0",
            "locator": {"uri": f"https://{host}/one"},
        }],
        "created_at": STAMP,
    })
    return registry


def _glossary(identifier="glossary:accountability"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "lemma": "accountability",
        "term_language": "en",
        "part_of_speech": "noun",
        "pronunciation": "/əˌkaʊntəˈbɪləti/",
        "senses": [
            {
                "language": "bn",
                "definition": "দায়বদ্ধতা",
                "usage_note": "কোনো কাজের জন্য জবাবদিহি করার বাধ্যবাধকতা।",
                "synonyms": ["জবাবদিহি"],
                "antonyms": ["দায়মুক্তি"],
                "collocations": ["public accountability"],
                "exam_tags": ["governance", "public administration"],
            },
            {
                "language": "en",
                "definition": "The state of being answerable for decisions or actions.",
                "usage_note": "Often used in governance and public administration.",
                "collocations": ["institutional accountability"],
                "exam_tags": ["governance"],
            },
        ],
        "status": "draft",
        "created_at": STAMP,
    }


def _vocabulary(identifier="learning:vocab"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "story_id": "story:one",
        "kind": "vocabulary",
        "language": "bn",
        "status": "draft",
        "content": {
            "term": "accountability",
            "glossary_entry_id": "glossary:accountability",
            "term_language": "en",
            "part_of_speech": "noun",
            "pronunciation": "/əˌkaʊntəˈbɪləti/",
            "meaning": "দায়বদ্ধতা",
            "context": "The report discusses public accountability.",
            "example": "Public accountability strengthens trust.",
            "exam_context": "BCS governance and public administration",
        },
        "claim_ids": ["claim:one"],
        "created_at": STAMP,
    }


def test_glossary_entry_persists_bilingual_senses_and_revision_history():
    registry = _prepared()
    try:
        entry = registry.add_glossary_entry(
            _glossary(), actor="lexicon-editor", reason="Create canonical bilingual headword."
        )
        assert entry.revision == 1
        assert registry.find_glossary_entry(
            "ACCOUNTABILITY", term_language="en", part_of_speech="noun"
        ).id == entry.id

        approved = _glossary()
        approved.update({
            "status": "approved",
            "reviewed_at": "2026-11-19T01:00:00Z",
            "reviewed_by": "lexicon-reviewer",
            "review_note": "Both Bangla and English senses were checked.",
        })
        revised = registry.revise_glossary_entry(
            entry.id, approved, expected_revision=1,
            actor="lexicon-reviewer", reason="Approve reviewed senses.",
        )
        assert revised.revision == 2
        assert revised.status == "approved"
        assert [item["revision"] for item in registry.glossary_entry_history(entry.id)] == [1, 2]
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_glossary_headword_is_unique_by_language_and_part_of_speech():
    registry = _prepared()
    try:
        registry.add_glossary_entry(
            _glossary(), actor="editor", reason="Create first entry."
        )
        duplicate = _glossary("glossary:duplicate")
        duplicate["lemma"] = "Accountability"
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            registry.add_glossary_entry(
                duplicate, actor="editor", reason="Duplicate headword."
            )
    finally:
        registry.close()


def test_vocabulary_learning_object_must_match_registered_glossary_entry():
    registry = _prepared()
    try:
        registry.add_glossary_entry(
            _glossary(), actor="editor", reason="Create controlled headword."
        )
        object_record = registry.add_learning_object(
            _vocabulary(), actor="editor", reason="Create source-context vocabulary item."
        )
        assert object_record.kind == "vocabulary"

        mismatch = _vocabulary("learning:vocab-mismatch")
        mismatch["content"]["term"] = "responsibility"
        with pytest.raises(RegistryIntegrityError, match="must match the referenced glossary entry"):
            registry.add_learning_object(
                mismatch, actor="editor", reason="Reject mismatched headword."
            )
        wrong_meaning = _vocabulary("learning:vocab-wrong-meaning")
        wrong_meaning["content"]["meaning"] = "দায়িত্ব"
        with pytest.raises(RegistryIntegrityError, match="meaning must match a glossary sense"):
            registry.add_learning_object(
                wrong_meaning, actor="editor", reason="Reject a meaning absent from the glossary."
            )
    finally:
        registry.close()


def test_approved_vocabulary_requires_approved_glossary_entry():
    registry = _prepared()
    try:
        entry = registry.add_glossary_entry(
            _glossary(), actor="editor", reason="Create controlled headword."
        )
        draft = registry.add_learning_object(
            _vocabulary(), actor="editor", reason="Create vocabulary draft."
        )
        draft_report = registry.learning_object_quality_report(draft.id)
        assert draft_report["meets_registry_quality_gates"] is False
        assert any(item["check"] == "controlled_glossary" for item in draft_report["blockers"])
        approved_object = _vocabulary()
        approved_object.update({
            "status": "approved",
            "reviewed_at": "2026-11-19T02:00:00Z",
            "reviewed_by": "reviewer",
            "review_note": "Vocabulary content reviewed.",
        })
        with pytest.raises(RegistryIntegrityError, match="requires an approved glossary entry"):
            registry.revise_learning_object(
                draft.id, approved_object, expected_revision=1,
                actor="reviewer", reason="Cannot approve against draft glossary.",
            )

        approved_entry = _glossary()
        approved_entry.update({
            "status": "approved",
            "reviewed_at": "2026-11-19T01:00:00Z",
            "reviewed_by": "lexicon-reviewer",
            "review_note": "Definitions reviewed.",
        })
        registry.revise_glossary_entry(
            entry.id, approved_entry, expected_revision=1,
            actor="lexicon-reviewer", reason="Approve glossary entry.",
        )
        result = registry.revise_learning_object(
            draft.id, approved_object, expected_revision=1,
            actor="reviewer", reason="Approve vocabulary against approved glossary.",
        )
        assert result.status == "approved"
        report = registry.learning_object_quality_report(result.id)
        assert report["meets_registry_quality_gates"] is True
        assert report["blockers"] == []
    finally:
        registry.close()


def test_glossary_revisions_are_append_only_and_stale_updates_are_rejected():
    registry = _prepared()
    try:
        entry = registry.add_glossary_entry(
            _glossary(), actor="editor", reason="Create glossary entry."
        )
        revision = _glossary()
        revision["senses"][0]["definition"] = "দায়বদ্ধ থাকা বা জবাবদিহি করার অবস্থা।"
        registry.revise_glossary_entry(
            entry.id, revision, expected_revision=1,
            actor="editor", reason="Clarify Bangla definition.",
        )
        with pytest.raises(RegistryIntegrityError, match="revision conflict"):
            registry.revise_glossary_entry(
                entry.id, revision, expected_revision=1,
                actor="editor-two", reason="Stale glossary edit.",
            )
        with pytest.raises(sqlite3.IntegrityError, match="glossary revisions are append-only"):
            registry._connection.execute(
                "DELETE FROM glossary_entry_revisions WHERE glossary_entry_id = ?",
                (entry.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="glossary entries cannot be deleted"):
            registry._connection.execute(
                "DELETE FROM glossary_entries WHERE id = ?", (entry.id,)
            )
    finally:
        registry.close()


def test_glossary_contract_requires_at_least_one_language_specific_sense():
    payload = _glossary("glossary:empty")
    payload["senses"] = []
    with pytest.raises(ValueError):
        validate_payload("glossary-entry", payload)


def test_sqlite_boundary_rejects_vocabulary_without_matching_glossary_sense():
    registry = _prepared()
    try:
        registry.add_glossary_entry(
            _glossary(), actor="editor", reason="Create canonical headword."
        )
        payload = _vocabulary("learning:vocab-direct")
        payload["content"]["meaning"] = "দায়িত্ব"
        with pytest.raises(sqlite3.IntegrityError, match="must match a registered glossary entry"):
            registry._connection.execute(
                """INSERT INTO learning_objects
                (id, story_id, kind, language, status, revision, created_at, updated_at,
                 last_actor, last_reason, payload_json)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (
                    payload["id"], payload["story_id"], payload["kind"], payload["language"],
                    payload["status"], payload["created_at"], payload["created_at"],
                    "editor", "Direct SQL guard test.", json.dumps(payload),
                ),
            )
    finally:
        registry.close()
