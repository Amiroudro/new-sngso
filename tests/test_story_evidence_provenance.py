"""Integrity tests for story, claim, and evidence provenance links."""

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
