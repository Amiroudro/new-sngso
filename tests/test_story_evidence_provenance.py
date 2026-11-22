"""Integrity tests for story, claim, and evidence provenance links."""

import sqlite3

import pytest

from sngso_engine import RegistryIntegrityError, SQLiteRegistry


STAMP = "2026-11-20T00:00:00Z"


def _source(registry, suffix):
    source_id = f"source:{suffix}"
    host = f"{suffix}.example.org"
    registry.add_source({
        "schema_version": "1.0.0",
        "id": source_id,
        "publisher_name": suffix.title(),
        "publisher_type": "newspaper",
        "canonical_homepage": f"https://{host}/",
        "domains": [host],
        "status": "active",
        "created_at": STAMP,
        "updated_at": STAMP,
    })
    return source_id, host


def _article(registry, source_id, host, suffix):
    article_id = f"article:{suffix}"
    registry.add_domain_record("article", {
        "schema_version": "1.0.0",
        "id": article_id,
        "source_id": source_id,
        "canonical_uri": f"https://{host}/news/{suffix}",
        "title": f"Report {suffix}",
        "source_language": "bn",
        "discovered_at": STAMP,
        "record_status": "active",
    })
    return article_id


def _story(registry, article_ids):
    registry.add_domain_record("story", {
        "schema_version": "1.0.0",
        "id": "story:shared-event",
        "canonical_title": "একটি ঘটনার বিবরণ",
        "status": "emerging",
        "created_at": STAMP,
        "updated_at": STAMP,
        "article_ids": article_ids,
    })


def _claim(registry, source_id, article_id, host, *, observation_id=None):
    provenance = {
        "source_id": source_id,
        "article_id": article_id,
        "observed_at": STAMP,
        "method": "human_review",
        "method_version": "1.0",
        "locator": {"uri": f"https://{host}/news/{article_id.split(':', 1)[1]}"},
    }
    if observation_id is not None:
        provenance["observation_id"] = observation_id
    return {
        "schema_version": "1.0.0",
        "id": "claim:event-date",
        "story_id": "story:shared-event",
        "text": "The event occurred on the stated date.",
        "language": "en",
        "assessment": "unassessed",
        "provenance": [provenance],
        "created_at": STAMP,
    }


def _evidence(claim_id, source_id, article_id, host, *, observation_id=None):
    payload = {
        "schema_version": "1.0.0",
        "id": "evidence:source-a",
        "claim_id": claim_id,
        "source_id": source_id,
        "article_id": article_id,
        "relationship": "supports",
        "locator": {"uri": f"https://{host}/news/{article_id.split(':', 1)[1]}"},
        "assessed_at": STAMP,
    }
    if observation_id is not None:
        payload["observation_id"] = observation_id
    return payload


def _prepared_registry():
    registry = SQLiteRegistry()
    source_a, host_a = _source(registry, "publisher-a")
    source_b, host_b = _source(registry, "publisher-b")
    article_a = _article(registry, source_a, host_a, "publisher-a-one")
    article_b = _article(registry, source_b, host_b, "publisher-b-one")
    _story(registry, [article_a, article_b])
    registry.add_domain_record("claim", _claim(registry, source_a, article_a, host_a))
    return registry, source_a, host_a, article_a, source_b, host_b, article_b


def test_evidence_must_match_the_claims_source_and_article_provenance():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        record = registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        assert record.claim_id == "claim:event-date"
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_evidence_from_another_publisher_is_not_attached_to_claim():
    registry, _, _, _, source_b, host_b, article_b = _prepared_registry()
    try:
        with pytest.raises(RegistryIntegrityError, match="not present in claim provenance"):
            registry.add_domain_record(
                "evidence",
                _evidence("claim:event-date", source_b, article_b, host_b),
            )
        assert registry.get_domain_record("evidence", "evidence:source-a") is None
    finally:
        registry.close()


def test_evidence_cannot_cite_another_article_from_the_same_source():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        other_article = _article(registry, source_a, host_a, "publisher-a-two")
        with pytest.raises(RegistryIntegrityError, match="not present in claim provenance"):
            registry.add_domain_record(
                "evidence",
                _evidence("claim:event-date", source_a, other_article, host_a),
            )
    finally:
        registry.close()


def test_claim_provenance_article_must_belong_to_the_claim_story():
    registry = SQLiteRegistry()
    try:
        source_a, host_a = _source(registry, "publisher-a")
        source_b, host_b = _source(registry, "publisher-b")
        article_a = _article(registry, source_a, host_a, "publisher-a-one")
        article_b = _article(registry, source_b, host_b, "publisher-b-one")
        _story(registry, [article_a])
        with pytest.raises(RegistryIntegrityError, match="not linked to its story"):
            registry.add_domain_record(
                "claim",
                _claim(registry, source_b, article_b, host_b),
            )
    finally:
        registry.close()


def test_claim_provenance_rejects_unknown_observation_ids():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        with pytest.raises(RegistryIntegrityError, match="observation is missing"):
            registry.add_domain_record(
                "claim",
                {
                    **_claim(registry, source_a, article_a, host_a),
                    "id": "claim:unknown-observation",
                    "provenance": [{
                        **_claim(registry, source_a, article_a, host_a)["provenance"][0],
                        "observation_id": "observation:not-found",
                    }],
                },
            )
    finally:
        registry.close()


def test_story_revision_cannot_orphan_existing_claim_provenance():
    registry, *_ = _prepared_registry()
    try:
        story = registry.get_domain_record("story", "story:shared-event")
        payload = story.to_payload()
        payload["article_ids"] = []
        with pytest.raises(RegistryIntegrityError, match="would orphan existing claim provenance"):
            registry.revise_domain_record(
                "story",
                story.id,
                payload,
                expected_revision=1,
                actor="editor",
                reason="Attempted to remove an article still cited by a claim.",
            )
    finally:
        registry.close()



def test_claim_revision_cannot_remove_provenance_used_by_existing_evidence():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        claim = registry.get_domain_record("claim", "claim:event-date")
        payload = claim.to_payload()
        payload["provenance"] = [{
            "source_id": source_a,
            "observed_at": STAMP,
            "method": "human_review",
            "method_version": "1.0",
        }]
        with pytest.raises(RegistryIntegrityError, match="would orphan existing evidence provenance"):
            registry.revise_domain_record(
                "claim",
                claim.id,
                payload,
                expected_revision=1,
                actor="editor",
                reason="Attempted to remove the article supporting existing evidence.",
            )
    finally:
        registry.close()


def test_evidence_source_article_and_locator_identity_cannot_be_rewritten():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        evidence = registry.get_domain_record("evidence", "evidence:source-a")
        payload = evidence.to_payload()
        other_article = _article(registry, source_a, host_a, "publisher-a-two")
        payload["article_id"] = other_article
        payload["locator"] = {"uri": f"https://{host_a}/news/publisher-a-two"}
        with pytest.raises(RegistryIntegrityError, match="identity links cannot be rewritten"):
            registry.revise_domain_record(
                "evidence",
                evidence.id,
                payload,
                expected_revision=1,
                actor="editor",
                reason="Attempted to reassign evidence to another article.",
            )
    finally:
        registry.close()



def _review_claim(registry, evidence_ids, *, assessment="supported", reviewed_at=STAMP, review_note="Reviewed against recorded evidence."):
    claim = registry.get_domain_record("claim", "claim:event-date")
    payload = claim.to_payload()
    payload["evidence_ids"] = evidence_ids
    payload["assessment"] = assessment
    if reviewed_at is not None:
        payload["reviewed_at"] = reviewed_at
    if review_note is not None:
        payload["review_note"] = review_note
    return registry.revise_domain_record(
        "claim",
        claim.id,
        payload,
        expected_revision=1,
        actor="editor",
        reason="Human assessment after evidence review.",
    )


def test_human_claim_assessment_requires_review_metadata_and_linked_evidence():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        evidence = registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        with pytest.raises(RegistryIntegrityError, match="review timestamp and a non-empty review note"):
            _review_claim(registry, [evidence.id], reviewed_at=None, review_note=None)
    finally:
        registry.close()


def test_supported_claim_requires_a_complete_evidence_list():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        first = registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        second_payload = _evidence("claim:event-date", source_a, article_a, host_a)
        second_payload["id"] = "evidence:source-a-second"
        second_payload["relationship"] = "qualifies"
        second = registry.add_domain_record("evidence", second_payload)
        with pytest.raises(RegistryIntegrityError, match="every evidence record linked"):
            _review_claim(registry, [first.id])
        assert second.id != first.id
    finally:
        registry.close()


def test_new_evidence_requires_claim_to_be_reset_before_review():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        first = registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        reviewed = _review_claim(registry, [first.id])
        assert reviewed.assessment == "supported"

        second_payload = _evidence("claim:event-date", source_a, article_a, host_a)
        second_payload["id"] = "evidence:source-a-contradiction"
        second_payload["relationship"] = "contradicts"
        with pytest.raises(RegistryIntegrityError, match="reset to unassessed"):
            registry.add_domain_record("evidence", second_payload)

        claim = registry.get_domain_record("claim", "claim:event-date")
        reset_payload = claim.to_payload()
        reset_payload["assessment"] = "unassessed"
        registry.revise_domain_record(
            "claim",
            claim.id,
            reset_payload,
            expected_revision=2,
            actor="editor",
            reason="Reopened assessment after new reporting became available.",
        )
        second = registry.add_domain_record("evidence", second_payload)
        claim = registry.get_domain_record("claim", "claim:event-date")
        final_payload = claim.to_payload()
        final_payload["evidence_ids"] = [first.id, second.id]
        final_payload["assessment"] = "disputed"
        final_payload["reviewed_at"] = STAMP
        final_payload["review_note"] = "Supporting and contradicting reports were reviewed."
        final = registry.revise_domain_record(
            "claim",
            claim.id,
            final_payload,
            expected_revision=3,
            actor="editor",
            reason="Reassessed after contradictory evidence.",
        )
        assert final.assessment == "disputed"
        assert registry.verify_audit_chain()
    finally:
        registry.close()



def test_evidence_relationship_cannot_change_while_claim_is_reviewed():
    registry, source_a, host_a, article_a, *_ = _prepared_registry()
    try:
        evidence = registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        _review_claim(registry, [evidence.id])
        current = registry.get_domain_record("evidence", evidence.id)
        payload = current.to_payload()
        payload["relationship"] = "contradicts"
        with pytest.raises(RegistryIntegrityError, match="reset to unassessed"):
            registry.revise_domain_record(
                "evidence",
                evidence.id,
                payload,
                expected_revision=1,
                actor="editor",
                reason="Attempted to change an evidence relationship after claim review.",
            )
    finally:
        registry.close()



def _story_decision(operation, story_ids, canonical_story_id, article_ids, *, split_to=None):
    assignments = []
    for index, article_id in enumerate(article_ids):
        if operation == "merge":
            target = canonical_story_id
        elif split_to is not None:
            target = split_to
        else:
            target = canonical_story_id if index == 0 else story_ids[-1]
        assignments.append({
            "article_id": article_id,
            "assigned_story_id": target,
        })
    return {
        "schema_version": "1.0.0",
        "id": f"story-decision:{operation}-001",
        "operation": operation,
        "story_ids": story_ids,
        "canonical_story_id": canonical_story_id,
        "expected_story_revisions": [
            {"story_id": story_id, "expected_revision": 1}
            for story_id in story_ids
        ],
        "article_assignments": assignments,
        "claim_assignments": [{
            "claim_id": "claim:event-date",
            "assigned_story_id": canonical_story_id,
            "expected_revision": 1,
        }],
        "decided_by": "senior-editor",
        "rationale": "Human review found these reports describe the same underlying event.",
        "decided_at": STAMP,
    }


def _add_second_story(registry, article_ids):
    registry.add_domain_record("story", {
        "schema_version": "1.0.0",
        "id": "story:secondary",
        "canonical_title": "সম্পর্কিত ঘটনার বিবরণ",
        "status": "developing",
        "created_at": STAMP,
        "updated_at": STAMP,
        "article_ids": article_ids,
    })


def test_story_merge_decision_is_append_only_and_visible_in_each_story_history():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        decision = registry.add_domain_record(
            "story_decision",
            _story_decision(
                "merge",
                ["story:shared-event", "story:secondary"],
                "story:shared-event",
                [article_a, article_b],
            ),
        )
        assert decision.operation == "merge"
        assert registry.story_decision_history("story:shared-event") == [decision.to_payload()]
        assert registry.story_decision_history("story:secondary") == [decision.to_payload()]
        assert registry.verify_audit_chain()
        with pytest.raises(RegistryIntegrityError, match="decision records are immutable"):
            registry.revise_domain_record(
                "story_decision",
                decision.id,
                decision.to_payload(),
                expected_revision=1,
                actor="senior-editor",
                reason="Attempted to rewrite a recorded merge decision.",
            )
    finally:
        registry.close()


def test_story_split_decision_requires_multiple_articles_and_is_audited():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        decision = registry.add_domain_record(
            "story_decision",
            _story_decision(
                "split",
                ["story:shared-event", "story:secondary"],
                "story:shared-event",
                [article_a, article_b],
            ),
        )
        assert decision.operation == "split"
        assert registry.story_decision_history("story:shared-event") == [decision.to_payload()]
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_story_decision_cannot_reference_unknown_story_or_unlinked_article():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        with pytest.raises(RegistryIntegrityError, match="unknown story"):
            registry.add_domain_record(
                "story_decision",
                _story_decision(
                    "merge",
                    ["story:shared-event", "story:missing"],
                    "story:shared-event",
                    [article_a, article_b],
                ),
            )
        _add_second_story(registry, [article_b])
        unrelated_article = _article(
            registry,
            "source:publisher-a",
            "publisher-a.example.org",
            "publisher-a-unlinked",
        )
        unrelated_article_b = _article(
            registry,
            "source:publisher-a",
            "publisher-a.example.org",
            "publisher-a-unlinked-two",
        )
        with pytest.raises(RegistryIntegrityError, match="must belong to at least one affected story"):
            registry.add_domain_record(
                "story_decision",
                _story_decision(
                    "merge",
                    ["story:shared-event", "story:secondary"],
                    "story:shared-event",
                    [article_a, article_b, unrelated_article, unrelated_article_b],
                ),
            )
    finally:
        registry.close()


def test_story_decision_canonical_story_must_be_in_the_decision_scope():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        with pytest.raises(RegistryIntegrityError, match="canonical story must be one"):
            registry.add_domain_record(
                "story_decision",
                _story_decision(
                    "merge",
                    ["story:shared-event", "story:secondary"],
                    "story:unrelated",
                    [article_a, article_b],
                ),
            )
    finally:
        registry.close()


def test_story_split_decision_must_allocate_articles_to_two_resulting_stories():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        with pytest.raises(RegistryIntegrityError, match="at least two resulting stories"):
            registry.add_domain_record(
                "story_decision",
                _story_decision(
                    "split",
                    ["story:shared-event", "story:secondary"],
                    "story:shared-event",
                    [article_a, article_b],
                    split_to="story:shared-event",
                ),
            )
    finally:
        registry.close()


def test_story_decision_history_rejects_unknown_story():
    registry = SQLiteRegistry()
    try:
        with pytest.raises(RegistryIntegrityError, match="unknown story"):
            registry.story_decision_history("story:missing")
    finally:
        registry.close()



def test_story_merge_decision_must_account_for_every_article_in_scope():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        article_c = _article(
            registry,
            "source:publisher-a",
            "publisher-a.example.org",
            "publisher-a-three",
        )
        _add_second_story(registry, [article_b, article_c])
        with pytest.raises(RegistryIntegrityError, match="account for every article"):
            registry.add_domain_record(
                "story_decision",
                _story_decision(
                    "merge",
                    ["story:shared-event", "story:secondary"],
                    "story:shared-event",
                    [article_a, article_b],
                ),
            )
    finally:
        registry.close()


def test_story_decision_rejects_stale_story_revision_preconditions():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        decision = _story_decision(
            "merge",
            ["story:shared-event", "story:secondary"],
            "story:shared-event",
            [article_a, article_b],
        )
        decision["expected_story_revisions"][0]["expected_revision"] = 2
        with pytest.raises(RegistryIntegrityError, match="expected revision is stale for story"):
            registry.add_domain_record("story_decision", decision)
    finally:
        registry.close()


def test_story_decision_rejects_stale_claim_revision_preconditions():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        decision = _story_decision(
            "merge",
            ["story:shared-event", "story:secondary"],
            "story:shared-event",
            [article_a, article_b],
        )
        decision["claim_assignments"][0]["expected_revision"] = 2
        with pytest.raises(RegistryIntegrityError, match="expected revision is stale for claim"):
            registry.add_domain_record("story_decision", decision)
    finally:
        registry.close()


def test_story_decision_must_account_for_every_claim():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        decision = _story_decision(
            "merge",
            ["story:shared-event", "story:secondary"],
            "story:shared-event",
            [article_a, article_b],
        )
        decision["claim_assignments"] = []
        with pytest.raises(RegistryIntegrityError, match="account for every claim"):
            registry.add_domain_record("story_decision", decision)
    finally:
        registry.close()


def test_split_decision_cannot_assign_claim_away_from_its_provenance_articles():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        decision = _story_decision(
            "split",
            ["story:shared-event", "story:secondary"],
            "story:shared-event",
            [article_a, article_b],
        )
        decision["claim_assignments"][0]["assigned_story_id"] = "story:secondary"
        with pytest.raises(RegistryIntegrityError, match="claim provenance does not fit"):
            registry.add_domain_record("story_decision", decision)
    finally:
        registry.close()


def test_story_decision_preview_is_read_only_and_has_stable_impact_fingerprint():
    registry, source_a, host_a, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        registry.add_domain_record(
            "evidence",
            _evidence("claim:event-date", source_a, article_a, host_a),
        )
        registry.add_domain_record("edition", {
            "schema_version": "1.0.0",
            "id": "edition:preview-impact",
            "edition_key": "2026-11-20",
            "created_at": STAMP,
            "status": "approved",
            "content_hash": "sha256:" + "a" * 64,
            "pipeline_run_id": "pipeline:fixture",
            "story_ids": ["story:shared-event"],
            "language_variants": [{"language": "en", "status": "approved", "text": "Historical staging candidate."}],
        })
        registry.add_domain_record(
            "story_decision",
            _story_decision(
                "merge",
                ["story:shared-event", "story:secondary"],
                "story:shared-event",
                [article_a, article_b],
            ),
        )
        before = registry.audit_checkpoint()
        first = registry.preview_story_decision_application("story-decision:merge-001")
        second = registry.preview_story_decision_application("story-decision:merge-001")
        after = registry.audit_checkpoint()

        assert first["applicable"] is True
        assert first["already_applied"] is False
        assert first["application"] is None
        assert first["blockers"] == []
        assert first["impact_fingerprint"].startswith("sha256:")
        assert first["impact_fingerprint"] == second["impact_fingerprint"]
        assert [item["article_id"] for item in first["articles"]] == [article_a, article_b]
        assert first["claims"][0]["assigned_story_id"] == "story:shared-event"
        assert first["evidence"][0]["evidence_id"] == "evidence:source-a"
        assert first["editions"] == [{
            "edition_id": "edition:preview-impact",
            "status": "approved",
            "content_hash": "sha256:" + "a" * 64,
            "affected_story_ids": ["story:shared-event"],
        }]
        assert before == after
        assert registry.verify_audit_chain()
    finally:
        registry.close()


def test_story_decision_preview_blocks_stale_story_revision():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        registry.add_domain_record(
            "story_decision",
            _story_decision(
                "merge",
                ["story:shared-event", "story:secondary"],
                "story:shared-event",
                [article_a, article_b],
            ),
        )
        story = registry.get_domain_record("story", "story:shared-event")
        changed = story.to_payload()
        changed["canonical_title"] = "Updated title after the editorial decision"
        registry.revise_domain_record(
            "story",
            story.id,
            changed,
            expected_revision=1,
            actor="editor",
            reason="Test stale decision preview.",
        )

        preview = registry.preview_story_decision_application("story-decision:merge-001")
        assert preview["applicable"] is False
        assert any("expected revision is stale for story" in item for item in preview["blockers"])
        assert preview["impact_fingerprint"].startswith("sha256:")
    finally:
        registry.close()


def test_story_decision_application_ledger_is_unique_and_append_only():
    registry, _, _, article_a, _, _, article_b = _prepared_registry()
    try:
        _add_second_story(registry, [article_b])
        registry.add_domain_record(
            "story_decision",
            _story_decision(
                "merge",
                ["story:shared-event", "story:secondary"],
                "story:shared-event",
                [article_a, article_b],
            ),
        )
        assert registry.schema_version() == 35

        registry._connection.execute(
            """INSERT INTO story_decision_applications
            (application_id, decision_id, applied_at, actor, rationale,
             decision_fingerprint, impact_fingerprint, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "story-application:fixture",
                "story-decision:merge-001",
                STAMP,
                "senior-editor",
                "Fixture verifies the append-only application ledger.",
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "{}",
            ),
        )
        preview = registry.preview_story_decision_application("story-decision:merge-001")
        assert preview["already_applied"] is True
        assert preview["applicable"] is False
        assert preview["application"]["application_id"] == "story-application:fixture"
        assert "story decision has already been applied" in preview["blockers"]

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            registry._connection.execute(
                """INSERT INTO story_decision_applications
                (application_id, decision_id, applied_at, actor, rationale,
                 decision_fingerprint, impact_fingerprint, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "story-application:duplicate",
                    "story-decision:merge-001",
                    STAMP,
                    "senior-editor",
                    "A second application must not be recorded.",
                    "sha256:" + "a" * 64,
                    "sha256:" + "b" * 64,
                    "{}",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="applications are append-only"):
            registry._connection.execute(
                "UPDATE story_decision_applications SET rationale = 'rewritten' "
                "WHERE application_id = 'story-application:fixture'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="applications are append-only"):
            registry._connection.execute(
                "DELETE FROM story_decision_applications "
                "WHERE application_id = 'story-application:fixture'"
            )
        assert registry.verify_audit_chain()
    finally:
        registry.close()
