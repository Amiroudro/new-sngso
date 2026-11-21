from __future__ import annotations

import sqlite3

import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry


STAMP = "2026-11-20T00:00:00Z"
ACCEPTED = "2026-11-20T01:00:00Z"


def _score_fixture(registry: SQLiteRegistry):
    registry.register_benchmark_principal(
        "curator:one", display_name="Curator One", roles=["benchmark_curator"],
        provisioned_by="deployment-operator", reason="Test curator provisioning",
    )
    registry.register_benchmark_principal(
        "reviewer:one", display_name="Reviewer One", roles=["benchmark_reviewer"],
        languages=["bn"], principal_kind="human",
        provisioned_by="deployment-operator", reason="Test human reviewer provisioning",
    )
    artifact = registry.register_benchmark_artifact(
        "artifact:bn:one", language="bn", actor="curator:one",
        reason="Register immutable test artifact",
        content={
            "schema_version": "1.0.0",
            "items": [{
                "item_id": "item:headline:one",
                "task_type": "headline",
                "input": "একটি পরীক্ষামূলক সংবাদ",
                "expected_output": "সঠিক শিরোনাম",
                "scoring_criteria": ["Factual accuracy"],
            }],
        },
    )
    assignment = {
        "schema_version": "1.0.0",
        "assignment_id": "assignment:one",
        "artifact_id": artifact["artifact_id"],
        "artifact_fingerprint": artifact["content_fingerprint"],
        "language": "bn",
        "reviewer_id": "reviewer:one",
        "assigned_by": "curator:one",
        "assigned_at": STAMP,
        "due_at": "2026-11-22T00:00:00Z",
        "status": "assigned",
        "reason": "Independent scoring assignment",
    }
    registry.create_benchmark_review_assignment(
        assignment, actor="curator:one", reason="Assign qualified reviewer",
    )
    registry.transition_benchmark_review_assignment(
        "assignment:one", event="accept", actor="reviewer:one",
        at=ACCEPTED, reason="Reviewer accepted assignment",
    )
    score = {
        "schema_version": "1.0.0",
        "benchmark_id": artifact["artifact_id"],
        "benchmark_fingerprint": artifact["content_fingerprint"],
        "item_id": "item:headline:one",
        "task_type": "headline",
        "language": "bn",
        "evaluator_id": "reviewer:one",
        "evaluated_at": "2026-11-20T01:01:00Z",
        "scale": {"minimum": 0, "maximum": 4},
        "criterion_scores": [{
            "criterion_id": "accuracy",
            "weight": 1.0,
            "score": 3,
            "anchor": "Mostly accurate with one minor omission",
            "rationale": "The headline preserves the central event but omits a useful qualifier.",
            "evidence_references": ["source:registered-item"],
        }],
    }
    identity = {
        "issuer": "https://login.example.test/",
        "subject": "oidc-reviewer-one",
        "provider_key_id": "test-key-1",
        "provider_adapter": "test-oidc-adapter/1",
        "authenticated_at": ACCEPTED,
    }
    return score, identity


def test_score_is_durably_bound_to_accepted_assignment_and_exact_artifact():
    with SQLiteRegistry() as registry:
        score, identity = _score_fixture(registry)
        stored = registry.add_benchmark_score(
            score, assignment_id="assignment:one", actor="reviewer:one",
            identity=identity, reason="Criterion score submitted",
        )
        assert stored["weighted_score"] == "3.0000"
        assert stored["eligible_for_comparison"] is True
        restored = registry.get_benchmark_score(stored["score_id"])
        assert restored["score"] == score
        assert restored["artifact_fingerprint"] == score["benchmark_fingerprint"]
        assert restored["subject"] == "oidc-reviewer-one"
        assert registry.list_benchmark_scores(artifact_id="artifact:bn:one") == [restored]


def test_score_rejects_item_or_task_not_in_registered_artifact():
    with SQLiteRegistry() as registry:
        score, identity = _score_fixture(registry)
        score["task_type"] = "translation"
        with pytest.raises(RegistryIntegrityError, match="item ID and task type"):
            registry.add_benchmark_score(
                score, assignment_id="assignment:one", actor="reviewer:one",
                identity=identity, reason="Wrong task binding",
            )


def test_score_records_cannot_be_updated_or_deleted():
    with SQLiteRegistry() as registry:
        score, identity = _score_fixture(registry)
        stored = registry.add_benchmark_score(
            score, assignment_id="assignment:one", actor="reviewer:one",
            identity=identity, reason="Criterion score submitted",
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "UPDATE benchmark_scores SET reason = 'tampered' WHERE score_id = ?",
                (stored["score_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM benchmark_scores WHERE score_id = ?",
                (stored["score_id"],),
            )


def test_registry_agreement_report_uses_two_distinct_persisted_human_scores():
    from copy import deepcopy

    with SQLiteRegistry() as registry:
        score, identity = _score_fixture(registry)
        registry.add_benchmark_score(
            score, assignment_id="assignment:one", actor="reviewer:one",
            identity=identity, reason="First independent score",
        )
        registry.register_benchmark_principal(
            "reviewer:two", display_name="Reviewer Two", roles=["benchmark_reviewer"],
            languages=["bn"], principal_kind="human",
            provisioned_by="deployment-operator", reason="Second human reviewer provisioning",
        )
        assignment = {
            "schema_version": "1.0.0",
            "assignment_id": "assignment:two",
            "artifact_id": score["benchmark_id"],
            "artifact_fingerprint": score["benchmark_fingerprint"],
            "language": "bn",
            "reviewer_id": "reviewer:two",
            "assigned_by": "curator:one",
            "assigned_at": STAMP,
            "due_at": "2026-11-22T00:00:00Z",
            "status": "assigned",
            "reason": "Independent second evaluator",
        }
        registry.create_benchmark_review_assignment(
            assignment, actor="curator:one", reason="Assign second evaluator",
        )
        registry.transition_benchmark_review_assignment(
            "assignment:two", event="accept", actor="reviewer:two",
            at=ACCEPTED, reason="Second reviewer accepted",
        )
        second = deepcopy(score)
        second["evaluator_id"] = "reviewer:two"
        second["criterion_scores"][0]["score"] = 2
        second_identity = dict(identity, subject="oidc-reviewer-two")
        registry.add_benchmark_score(
            second, assignment_id="assignment:two", actor="reviewer:two",
            identity=second_identity, reason="Second independent score",
        )
        report = registry.benchmark_evaluator_agreement_report(
            "artifact:bn:one", "item:headline:one"
        )
        assert report["evaluator_count"] == 2
        assert report["weighted_mean_absolute_score_difference"] == "1.0000"
        assert report["evaluators"] == ["reviewer:one", "reviewer:two"]
