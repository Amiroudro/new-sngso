"""Persistence, language variants, and editorial review tests for learning objects."""

import json
import sqlite3

import json
import pytest

from sngso_engine import RegistryIntegrityError, SQLiteRegistry, validate_payload

STAMP = "2026-11-19T00:00:00Z"


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
    registry.add_domain_record("claim", {
        "schema_version": "1.0.0",
        "id": "claim:one",
        "story_id": "story:one",
        "text": "The authority issued a public notice.",
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


def _summary(identifier="learning:summary-bn", language="bn"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "story_id": "story:one",
        "kind": "brief_summary",
        "language": language,
        "status": "draft",
        "content": {"text": "কর্তৃপক্ষ একটি সরকারি বিজ্ঞপ্তি জারি করেছে।", "segments": [{"text": "কর্তৃপক্ষ একটি সরকারি বিজ্ঞপ্তি জারি করেছে।", "claim_ids": ["claim:one"], "role": "fact"}]},
        "claim_ids": ["claim:one"],
        "created_at": STAMP,
    }


def test_learning_object_is_persisted_with_initial_revision_and_audit():
    registry = _prepared()
    try:
        record = registry.add_learning_object(
            _summary(), actor="editor:one", reason="Create the first Bangla summary draft."
        )
        assert record.revision == 1
        assert record.status == "draft"
        assert registry.get_learning_object(record.id) == record
        history = registry.learning_object_history(record.id)
        assert len(history) == 1
        assert history[0]["actor"] == "editor:one"
        assert history[0]["reason"] == "Create the first Bangla summary draft."
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_bangla_and_english_learning_objects_have_independent_review_states():
    registry = _prepared()
    try:
        bn = registry.add_learning_object(
            _summary("learning:summary-bn", "bn"),
            actor="editor:bn",
            reason="Draft the Bangla variant.",
        )
        en_payload = _summary("learning:summary-en", "en")
        en_payload["content"] = {"text": "The authority issued a public notice.", "segments": [{"text": "The authority issued a public notice.", "claim_ids": ["claim:one"], "role": "fact"}]}
        en = registry.add_learning_object(
            en_payload, actor="editor:en", reason="Draft the English variant."
        )
        assert bn.status == en.status == "draft"
        assert {item.id for item in registry.story_learning_objects("story:one")} == {
            "learning:summary-bn", "learning:summary-en"
        }
    finally:
        registry.close()


def test_vocabulary_and_question_content_require_kind_specific_fields():
    vocabulary = _summary("learning:vocab")
    vocabulary.update({
        "kind": "vocabulary",
        "content": {
            "term": "accountability",
            "glossary_entry_id": "glossary:accountability",
            "term_language": "en",
            "part_of_speech": "noun",
            "meaning": "দায়বদ্ধতা",
            "context": "The report discusses public accountability.",
            "example": "Public accountability requires transparent decisions.",
        },
    })
    validate_payload("learning-object", vocabulary)

    question = _summary("learning:question")
    question.update({
        "kind": "comprehension_question",
        "content": {
            "question": "What did the authority issue?",
            "answer": "A public notice.",
            "explanation": "The linked claim records the notice.",
            "answer_claim_ids": ["claim:one"],
        },
    })
    validate_payload("learning-object", question)

    invalid = {**vocabulary, "id": "learning:vocab-invalid", "content": {"term": "accountability"}}
    with pytest.raises(ValueError):
        validate_payload("learning-object", invalid)


def test_learning_object_requires_claims_from_the_same_story():
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
            "text": "A separate proposition.",
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
        payload = _summary("learning:cross-story")
        payload["claim_ids"] = ["claim:other"]
        payload["content"]["segments"][0]["claim_ids"] = ["claim:other"]
        with pytest.raises(RegistryIntegrityError, match="claims must belong to its story"):
            registry.add_learning_object(
                payload, actor="editor", reason="This must be rejected."
            )
    finally:
        registry.close()


def test_learning_object_approval_requires_review_and_revision_history():
    registry = _prepared()
    try:
        draft = registry.add_learning_object(
            _summary(), actor="editor:one", reason="Create draft."
        )
        approved = _summary()
        approved.update({
            "status": "approved",
            "reviewed_at": "2026-11-20T01:00:00Z",
            "reviewed_by": "reviewer:senior",
            "review_note": "The summary is accurate and supported by the linked claim.",
        })
        revised = registry.revise_learning_object(
            draft.id,
            approved,
            expected_revision=1,
            actor="reviewer:senior",
            reason="Approve after factual and language review.",
        )
        assert revised.revision == 2
        assert revised.status == "approved"
        history = registry.learning_object_history(draft.id)
        assert [item["revision"] for item in history] == [1, 2]
        assert history[1]["record"]["reviewed_by"] == "reviewer:senior"
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_learning_object_revision_rejects_stale_revision_and_preserves_history():
    registry = _prepared()
    try:
        draft = registry.add_learning_object(
            _summary(), actor="editor:one", reason="Create draft."
        )
        revised_payload = _summary()
        revised_payload["content"] = {"text": "সংশোধিত বাংলা সারাংশ।", "segments": [{"text": "সংশোধিত বাংলা সারাংশ।", "claim_ids": ["claim:one"], "role": "fact"}]}
        registry.revise_learning_object(
            draft.id, revised_payload, expected_revision=1,
            actor="editor:one", reason="Clarify the wording.",
        )
        with pytest.raises(RegistryIntegrityError, match="revision conflict"):
            registry.revise_learning_object(
                draft.id, revised_payload, expected_revision=1,
                actor="editor:two", reason="Stale edit.",
            )
        assert len(registry.learning_object_history(draft.id)) == 2
        assert registry.get_learning_object(draft.id).revision == 2
    finally:
        registry.close()


def test_learning_object_review_cannot_predate_creation():
    registry = _prepared()
    try:
        draft = registry.add_learning_object(
            _summary(), actor="editor:one", reason="Create draft."
        )
        payload = _summary()
        payload.update({
            "status": "rejected",
            "reviewed_at": "2026-11-18T23:00:00Z",
            "reviewed_by": "reviewer",
            "review_note": "Review predates creation.",
        })
        with pytest.raises(RegistryIntegrityError, match="review cannot predate its creation"):
            registry.revise_learning_object(
                draft.id, payload, expected_revision=1,
                actor="reviewer", reason="Reject the draft.",
            )
    finally:
        registry.close()


def test_learning_object_revision_history_is_append_only():
    registry = _prepared()
    try:
        record = registry.add_learning_object(
            _summary(), actor="editor:one", reason="Create draft."
        )
        with pytest.raises(sqlite3.IntegrityError, match="learning object revisions are append-only"):
            registry._connection.execute(
                "UPDATE learning_object_revisions SET reason = 'rewritten' WHERE learning_object_id = ?",
                (record.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="learning object revisions are append-only"):
            registry._connection.execute(
                "DELETE FROM learning_object_revisions WHERE learning_object_id = ?",
                (record.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="learning objects cannot be deleted"):
            registry._connection.execute(
                "DELETE FROM learning_objects WHERE id = ?", (record.id,)
            )
    finally:
        registry.close()


def test_sqlite_rejects_learning_object_update_without_next_revision():
    registry = _prepared()
    try:
        record = registry.add_learning_object(
            _summary(), actor="editor:one", reason="Create draft."
        )
        with pytest.raises(sqlite3.IntegrityError, match="revision must increment by one"):
            registry._connection.execute(
                "UPDATE learning_objects SET status = 'review_required' WHERE id = ?",
                (record.id,),
            )
    finally:
        registry.close()


def test_summary_segments_must_match_text_and_top_level_claim_links():
    registry = _prepared()
    try:
        mismatched_text = _summary("learning:bad-text")
        mismatched_text["content"]["text"] = "This sentence is not represented by the segment."
        with pytest.raises(RegistryIntegrityError, match="exactly match its claim-mapped segments"):
            registry.add_learning_object(
                mismatched_text, actor="editor", reason="Invalid traceability fixture."
            )

        outside_claim = _summary("learning:outside-claim")
        outside_claim["content"]["segments"][0]["claim_ids"] = ["claim:outside"]
        with pytest.raises(RegistryIntegrityError, match="claim outside the learning object"):
            registry.add_learning_object(
                outside_claim, actor="editor", reason="Invalid claim link fixture."
            )
    finally:
        registry.close()


def test_summary_requires_segments_and_attributed_segments_require_attribution():
    missing_segments = _summary("learning:no-segments")
    missing_segments["content"] = {"text": "An unlinked summary."}
    with pytest.raises(ValueError):
        validate_payload("learning-object", missing_segments)

    missing_attribution = _summary("learning:no-attribution")
    missing_attribution["content"]["segments"][0]["role"] = "attributed"
    with pytest.raises(ValueError):
        validate_payload("learning-object", missing_attribution)


def test_question_answer_claim_links_must_be_in_the_learning_object_claim_set():
    registry = _prepared()
    try:
        question = _summary("learning:unlinked-answer")
        question.update({
            "kind": "comprehension_question",
            "content": {
                "question": "What did the authority issue?",
                "answer": "A public notice.",
                "explanation": "The answer is supported by a claim.",
                "answer_claim_ids": ["claim:outside"],
            },
        })
        with pytest.raises(RegistryIntegrityError, match="answer claim links must be a non-empty subset"):
            registry.add_learning_object(
                question, actor="editor", reason="Reject unsupported answer link."
            )
    finally:
        registry.close()


def test_question_schema_requires_answer_claim_links():
    question = _summary("learning:question-without-answer-links")
    question.update({
        "kind": "analysis_question",
        "content": {
            "question": "Why did the authority act?",
            "answer": "The evidence indicates a policy concern.",
            "explanation": "The linked claim supports this interpretation.",
        },
    })
    with pytest.raises(ValueError):
        validate_payload("learning-object", question)


def _support_claim_with_evidence(registry):
    registry.add_domain_record("evidence", {
        "schema_version": "1.0.0",
        "id": "evidence:one",
        "claim_id": "claim:one",
        "source_id": "source:publisher",
        "article_id": "article:one",
        "relationship": "supports",
        "locator": {"uri": "https://publisher.example.org/one"},
        "assessed_at": "2026-11-19T01:00:00Z",
        "assessed_by": "reviewer",
    })
    claim_row = registry._connection.execute(
        "SELECT payload_json FROM domain_records WHERE record_type = 'claim' AND id = 'claim:one'"
    ).fetchone()
    claim = json.loads(claim_row["payload_json"])
    claim.update({
        "assessment": "supported",
        "evidence_ids": ["evidence:one"],
        "reviewed_at": "2026-11-19T02:00:00Z",
        "review_note": "The linked evidence directly supports this proposition.",
    })
    registry.revise_domain_record(
        "claim", "claim:one", claim, expected_revision=1,
        actor="reviewer", reason="Assess the claim against linked evidence.",
    )


def _approve_learning_object(registry, payload):
    draft = registry.add_learning_object(
        payload, actor="editor", reason="Create editorial draft."
    )
    approved = draft.to_payload()
    approved.update({
        "status": "approved",
        "reviewed_at": "2026-11-19T03:00:00Z",
        "reviewed_by": "reviewer",
        "review_note": "The content and claim links were reviewed.",
    })
    return registry.revise_learning_object(
        draft.id, approved, expected_revision=1,
        actor="reviewer", reason="Approve after quality review.",
    )


def test_quality_report_passes_supported_approved_summary_without_writing_audit():
    registry = _prepared()
    try:
        _support_claim_with_evidence(registry)
        _approve_learning_object(registry, _summary())
        before = registry._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        report = registry.learning_object_quality_report("learning:summary-bn")
        after = registry._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        assert report["meets_registry_quality_gates"] is True
        assert report["blockers"] == []
        assert before == after
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_quality_report_blocks_factual_summary_using_unassessed_claim():
    registry = _prepared()
    try:
        _approve_learning_object(registry, _summary())
        report = registry.learning_object_quality_report("learning:summary-bn")
        assert report["meets_registry_quality_gates"] is False
        assert any(item["check"] == "factual_segment_assessment" for item in report["blockers"])
    finally:
        registry.close()


def test_quality_report_requires_supported_claims_for_question_answers():
    registry = _prepared()
    try:
        question = _summary("learning:question")
        question.update({
            "kind": "comprehension_question",
            "content": {
                "question": "What did the authority issue?",
                "answer": "A public notice.",
                "explanation": "The linked claim records the notice.",
                "answer_claim_ids": ["claim:one"],
            },
        })
        _approve_learning_object(registry, question)
        report = registry.learning_object_quality_report("learning:question")
        assert report["meets_registry_quality_gates"] is False
        assert any(item["check"] == "answer_support" for item in report["blockers"])
    finally:
        registry.close()


def _mcq(identifier="learning:mcq"):
    return {
        "schema_version": "1.0.0",
        "id": identifier,
        "story_id": "story:one",
        "kind": "mcq_question",
        "language": "en",
        "status": "draft",
        "content": {
            "question": "Which statement best describes public accountability?",
            "explanation": "The linked claim provides the factual basis for the answer.",
            "answer_claim_ids": ["claim:one"],
            "options": [
                {"id": "A", "text": "Being answerable for decisions and actions"},
                {"id": "B", "text": "Avoiding public disclosure"},
                {"id": "C", "text": "Removing institutional oversight"},
                {"id": "D", "text": "Delegating all responsibility"},
            ],
            "correct_option_id": "A",
            "difficulty": "moderate",
        },
        "claim_ids": ["claim:one"],
        "created_at": STAMP,
    }


def test_mcq_learning_object_stores_one_keyed_correct_option_and_claim_links():
    registry = _prepared()
    try:
        payload = _mcq()
        validate_payload("learning-object", payload)
        record = registry.add_learning_object(
            payload, actor="question-editor", reason="Create a source-linked MCQ draft."
        )
        assert record.kind == "mcq_question"
        assert record.to_payload()["content"]["correct_option_id"] == "A"
    finally:
        registry.close()


def test_mcq_learning_object_rejects_duplicate_option_ids_and_unknown_answer_key():
    registry = _prepared()
    try:
        duplicate = _mcq("learning:mcq-duplicate")
        duplicate["content"]["options"][1]["id"] = "A"
        with pytest.raises(RegistryIntegrityError, match="option IDs must be unique"):
            registry.add_learning_object(
                duplicate, actor="editor", reason="Reject duplicate option IDs."
            )

        duplicate_text = _mcq("learning:mcq-duplicate-text")
        duplicate_text["content"]["options"][1]["text"] = "Being answerable for decisions and actions"
        with pytest.raises(RegistryIntegrityError, match="option text must be unique"):
            registry.add_learning_object(
                duplicate_text, actor="editor", reason="Reject duplicate option text."
            )

        unknown_key = _mcq("learning:mcq-unknown-key")
        unknown_key["content"]["correct_option_id"] = "E"
        with pytest.raises(RegistryIntegrityError, match="correct option must reference"):
            registry.add_learning_object(
                unknown_key, actor="editor", reason="Reject unknown answer key."
            )
    finally:
        registry.close()


def test_sqlite_boundary_rejects_mcq_with_duplicate_option_ids():
    registry = _prepared()
    try:
        payload = _mcq("learning:mcq-direct-sql")
        payload["content"]["options"][1]["id"] = "A"
        with pytest.raises(sqlite3.IntegrityError, match="MCQ option IDs must be unique"):
            registry._connection.execute(
                """INSERT INTO learning_objects
                (id, story_id, kind, language, status, revision, created_at, updated_at,
                 last_actor, last_reason, payload_json)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (
                    payload["id"], payload["story_id"], payload["kind"], payload["language"],
                    payload["status"], payload["created_at"], payload["created_at"],
                    "editor", "Direct SQL MCQ guard.", json.dumps(payload),
                ),
            )
    finally:
        registry.close()
