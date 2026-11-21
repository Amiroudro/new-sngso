"""Single-snapshot story editorial dossier tests."""

import json

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
    claim_row = registry._connection.execute(
        "SELECT payload_json FROM domain_records WHERE record_type = 'claim' AND id = 'claim:a'"
    ).fetchone()
    claim = json.loads(claim_row["payload_json"])
    claim["evidence_ids"] = ["evidence:one"]
    registry.revise_domain_record(
        "claim", "claim:a", claim, expected_revision=1,
        actor="editor", reason="Attach the registered evidence locator.",
    )
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
        "content": {"text": "কর্তৃপক্ষ একটি বিজ্ঞপ্তি জারি করেছে।", "segments": [{"text": "কর্তৃপক্ষ একটি বিজ্ঞপ্তি জারি করেছে।", "claim_ids": ["claim:a"], "role": "fact"}]},
        "claim_ids": ["claim:a"],
        "created_at": STAMP,
    }, actor="editor", reason="Fixture learning object.")
    registry.add_glossary_entry({
        "schema_version": "1.0.0",
        "id": "glossary:accountability",
        "lemma": "accountability",
        "term_language": "en",
        "part_of_speech": "noun",
        "senses": [{"language": "bn", "definition": "দায়বদ্ধতা"}],
        "status": "draft",
        "created_at": STAMP,
    }, actor="lexicon-editor", reason="Fixture glossary entry.")
    registry.add_learning_object({
        "schema_version": "1.0.0",
        "id": "learning:vocabulary-bn",
        "story_id": "story:one",
        "kind": "vocabulary",
        "language": "bn",
        "status": "draft",
        "content": {
            "term": "accountability",
            "glossary_entry_id": "glossary:accountability",
            "source_evidence_id": "evidence:one",
            "source_evidence_revision": 1,
            "term_language": "en",
            "part_of_speech": "noun",
            "meaning": "দায়বদ্ধতা",
            "context": "The report discusses accountability.",
            "example": "Public accountability strengthens trust."
        },
        "claim_ids": ["claim:a"],
        "created_at": STAMP,
    }, actor="editor", reason="Fixture vocabulary object.")
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
        assert len(first["learning_objects"]) == 2
        assert len(first["glossary_entries"]) == 1
        assert first["story_decisions"] == []
        assert first["benchmark_reviews"] == []
        assert first["benchmark_acceptance_reviews"] == []
        assert first["fingerprint"].startswith("sha256:")
        assert first["fingerprint"] == second["fingerprint"]
        assert before == after
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_bilingual_quality_report_keeps_language_coverage_and_blockers_separate():
    registry = _prepared()
    try:
        report = registry.bilingual_editorial_quality_report("story:one")
        assert report["languages"]["bn"]["artifact_count"] == 2
        assert report["languages"]["en"]["artifact_count"] == 0
        assert report["languages"]["bn"]["approved_count"] == 0
        assert report["languages"]["bn"]["quality_gate_pass_count"] == 0
        assert report["languages"]["en"]["blocked_count"] == 0
        assert report["kind_coverage"] == [
            {"kind": "brief_summary", "bangla_present": True, "english_present": False, "paired": False},
            {"kind": "vocabulary", "bangla_present": True, "english_present": False, "paired": False},
        ]
        assert report["missing_language_counterparts"] == [
            {"kind": "brief_summary", "missing_language": "en"},
            {"kind": "vocabulary", "missing_language": "en"},
        ]
        assert report["overall_quality_score"] is None
        assert report["production_ready"] is False
        assert report["authorization_granted"] is False
    finally:
        registry.close()


def test_narrative_assembly_traces_segments_and_keeps_language_and_conflicts_separate():
    registry = _prepared()
    try:
        report = registry.story_narrative_assembly_report("story:one")
        assert set(report["languages"]) == {"bn", "en"}
        bangla = report["languages"]["bn"]
        assert len(bangla["segments"]) == 1
        segment = bangla["segments"][0]
        assert segment["role"] == "fact"
        assert segment["claim_ids"] == ["claim:a"]
        assert segment["support_state"] == "unverified_or_disputed"
        assert segment["claims"][0]["provenance"][0]["article_id"] == "article:one"
        assert segment["claims"][0]["evidence"][0]["evidence_id"] == "evidence:one"
        assert bangla["blockers"]
        assert bangla["assembly_status"] == "blocked"
        assert bangla["summary"]["factual_segment_count"] == 1
        assert bangla["summary"]["unsupported_factual_segment_count"] == 1
        assert report["languages"]["en"]["publisher_perspectives"][0]["perspective_id"] == "perspective:one"
        assert len(report["unresolved_contradictions"]) == 1
        assert report["unresolved_contradictions"][0]["relation_id"] == "claim-relation:one"
        assert report["languages"]["bn"]["production_ready"] is False
        assert report["production_ready"] is False
        assert report["authorization_granted"] is False
    finally:
        registry.close()


def test_narrative_assembly_is_embedded_in_snapshot_without_changing_source_evidence():
    registry = _prepared()
    try:
        snapshot = registry.story_editorial_snapshot("story:one")
        report = snapshot["narrative_assembly_report"]
        assert report["assembly_policy"]["attributed_positions_remain_separate"] is True
        assert report["assembly_policy"]["bangla_and_english_are_assessed_independently"] is True
        assert "snapshot_fingerprint" not in report
        assert snapshot["fingerprint"]
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_source_independence_report_does_not_confuse_distinct_articles_with_independent_reporting():
    registry = _prepared()
    try:
        report = registry.story_source_independence_evidence_report("story:one")
        assert report["independence_status"] == "not_established"
        assert report["independent_reporting_count"] is None
        assert report["articles"][0]["duplicate_review_status"] == "not_reviewed"
        assert report["articles"][0]["independent_reporting_status"] == "not_established"
        assert report["articles"][0]["independence_evidence"]["syndication_or_republication"] == "not_assessed"
        assert report["production_ready"] is False
        snapshot = registry.story_editorial_snapshot("story:one")
        assert snapshot["source_independence_evidence_report"]["independence_status"] == "not_established"
    finally:
        registry.close()


def test_narrative_assembly_blocks_rejected_language_review_even_when_review_record_exists():
    registry = _prepared()
    try:
        current = registry.get_learning_object("learning:summary-bn")
        payload = current.to_payload()
        payload.update({
            "status": "rejected",
            "reviewed_at": STAMP,
            "reviewed_by": "reviewer:bn",
            "review_note": "Needs language revision.",
            "language_review": {
                "language": "bn",
                "rubric_version": "bn-editorial-v1",
                "reviewer_id": "reviewer:bn",
                "reviewed_at": STAMP,
                "note": "Spelling and terminology require revision.",
                "dimensions": {
                    "grammar_and_spelling": "needs_revision",
                    "naturalness_and_register": "pass",
                    "terminology_consistency": "pass",
                    "factual_fidelity": "pass",
                    "attribution": "pass",
                    "omissions": "pass",
                    "translation_fidelity": "pass",
                },
            },
        })
        registry.revise_learning_object(
            "learning:summary-bn",
            payload,
            expected_revision=current.revision,
            actor="reviewer:bn",
            reason="Record rejected language review.",
        )
        report = registry.story_narrative_assembly_report("story:one")
        object_report = report["languages"]["bn"]["objects"][0]
        assert object_report["language_review_pass"] is False
        assert report["languages"]["bn"]["assembly_status"] == "blocked"
        assert report["languages"]["bn"]["summary"]["language_review_pass_count"] == 0
        assert any(
            blocker["reason"].startswith("language review is rejected")
            for blocker in report["languages"]["bn"]["blockers"]
        )
        assert report["production_ready"] is False
    finally:
        registry.close()


def test_editorial_snapshot_rejects_unknown_story():
    registry = _prepared()
    try:
        with pytest.raises(RegistryIntegrityError, match="unknown story"):
            registry.story_editorial_snapshot("story:missing")
    finally:
        registry.close()



def test_bilingual_report_surfaces_variant_claim_set_mismatches():
    registry = _prepared()
    try:
        def add_variant(identifier, language, group_id, claim_id, text):
            registry.add_learning_object({
                "schema_version": "1.0.0",
                "id": identifier,
                "story_id": "story:one",
                "kind": "brief_summary",
                "language": language,
                "variant_group_id": group_id,
                "status": "draft",
                "content": {"text": text, "segments": [{
                    "text": text,
                    "claim_ids": [claim_id],
                    "role": "fact",
                }]},
                "claim_ids": [claim_id],
                "created_at": STAMP,
            }, actor="editor", reason="Create a paired language variant.")

        add_variant("learning:matched-bn", "bn", "variant:matched", "claim:a", "বাংলা দাবি।")
        add_variant("learning:matched-en", "en", "variant:matched", "claim:a", "English claim.")
        add_variant("learning:mismatch-bn", "bn", "variant:mismatch", "claim:a", "বাংলা দাবি।")
        add_variant("learning:mismatch-en", "en", "variant:mismatch", "claim:b", "Different English claim.")

        report = registry.bilingual_editorial_quality_report("story:one")
        variants = {item["variant_group_id"]: item for item in report["variant_claim_coverage"]}
        assert variants["variant:matched"]["status"] == "matched"
        assert variants["variant:matched"]["claim_sets_match"] is True
        assert variants["variant:mismatch"]["status"] == "claim_set_mismatch"
        assert variants["variant:mismatch"]["bangla_only_claim_ids"] == ["claim:a"]
        assert variants["variant:mismatch"]["english_only_claim_ids"] == ["claim:b"]
        assert report["production_ready"] is False
        assert report["authorization_granted"] is False
    finally:
        registry.close()
