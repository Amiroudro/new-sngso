from copy import deepcopy
import sqlite3

import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry
from tests.test_benchmark_study_registry import _registered


STAMP = "2026-11-20T00:00:00Z"
ACCEPTED = "2026-11-20T00:05:00Z"
COMPLETED = "2026-11-20T03:30:00Z"
AUTHENTICATED = "2026-11-20T04:10:00Z"


def _score(registry, artifact, item_id, evaluator_id):
    assignment_id = f"assignment:{item_id}:{evaluator_id}"
    registry.create_benchmark_review_assignment(
        {
            "schema_version": "1.0.0", "assignment_id": assignment_id,
            "artifact_id": artifact["artifact_id"],
            "artifact_fingerprint": artifact["content_fingerprint"],
            "language": "bn", "reviewer_id": evaluator_id,
            "assigned_by": "curator:study", "assigned_at": STAMP,
            "due_at": "2026-11-20T04:00:00Z", "status": "assigned",
            "reason": "Blind study evaluator assignment",
        },
        actor="curator:study", reason="Assign blind study evaluator",
    )
    registry.transition_benchmark_review_assignment(
        assignment_id, event="accept", actor=evaluator_id, at=ACCEPTED,
        reason="Accept blind study assignment",
    )
    score = {
        "schema_version": "1.0.0", "benchmark_id": artifact["artifact_id"],
        "benchmark_fingerprint": artifact["content_fingerprint"],
        "item_id": item_id, "task_type": "headline", "language": "bn",
        "evaluator_id": evaluator_id, "evaluated_at": "2026-11-20T01:00:00Z",
        "scale": {"minimum": 0, "maximum": 4},
        "criterion_scores": [{
            "criterion_id": "accuracy", "weight": 1.0, "score": 3,
            "anchor": "Mostly accurate with a minor omission",
            "rationale": "The response preserves the central fact but omits a useful qualifier.",
            "evidence_references": ["source:registered-item"],
        }],
    }
    stored = registry.add_benchmark_score(
        score, assignment_id=assignment_id, actor=evaluator_id,
        identity={
            "issuer": "https://login.example.test/", "subject": f"oidc:{evaluator_id}",
            "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
            "authenticated_at": ACCEPTED,
        },
        reason="Submit blind study score",
    )
    return stored["score_id"]


def _execution(artifact, protocol, score_ids):
    return {
        "schema_version": "1.0.0", "execution_id": "execution:study:one",
        "study_id": protocol["study_id"], "study_version": protocol["study_version"],
        "protocol_fingerprint": protocol["protocol_fingerprint"],
        "benchmark_id": artifact["artifact_id"],
        "benchmark_fingerprint": artifact["content_fingerprint"],
        "rubric_version": protocol["rubric_version"],
        "started_at": "2026-11-20T00:30:00Z", "completed_at": COMPLETED,
        "score_records": [
            {"score_id": score_id, "item_id": item_id, "evaluator_id": evaluator_id}
            for score_id, (item_id, evaluator_id) in zip(
                score_ids,
                [("blind:1", "human:one"), ("blind:1", "human:two"),
                 ("blind:2", "human:one"), ("blind:2", "human:two")],
            )
        ],
        "missing_records": [],
        "analysis": {
            "measurement_scale": "ordinal", "statistic_name": "Ordinal Krippendorff alpha",
            "estimate": 0.81, "confidence_interval": {"lower": 0.72, "upper": 0.88},
            "confidence_level": 0.95, "sampled_item_count": 2,
            "scored_pair_count": 4, "missing_pair_count": 0,
            "analysis_artifact_fingerprint": "sha256:" + "c" * 64,
            "missing_data_handling": "All planned blind item/evaluator pairs were scored; no missingness was observed.",
            "independence_assessment": "Reviewers scored independently without access to peer scores or adjudication outcomes.",
            "methodologist_id": "methodologist:one",
            "methodologist_rationale": "The selected coefficient is suitable for ordinal ratings and the crossed rater/item design; the uncertainty interval and design limitations are reported.",
            "reviewed_at": "2026-11-20T03:45:00Z",
        },
        "limitations": ["The registry stores the analysis artifact fingerprint but does not independently reproduce its computation."]
    }


def test_execution_registry_binds_protocol_scores_and_independent_methodologist():
    with SQLiteRegistry() as registry:
        artifact, protocol = _registered(registry)
        stored_protocol = registry.register_benchmark_study_protocol(
            protocol, actor="curator:study", reason="Register prespecified study",
        )
        protocol["protocol_fingerprint"] = stored_protocol["protocol_fingerprint"]
        for reviewer_id in ("human:one", "human:two"):
            registry.register_benchmark_principal(
                reviewer_id, display_name=reviewer_id,
                roles=["benchmark_reviewer"], languages=["bn"], principal_kind="human",
                provisioned_by="deployment-operator", reason="Study reviewer",
            )
        registry.register_benchmark_principal(
            "methodologist:one", display_name="Independent Methodologist",
            roles=["benchmark_methodologist"], principal_kind="human",
            provisioned_by="deployment-operator", reason="Independent methods reviewer",
        )
        score_ids = [
            _score(registry, artifact, item_id, reviewer_id)
            for item_id in ("blind:1", "blind:2")
            for reviewer_id in ("human:one", "human:two")
        ]
        payload = _execution(artifact, protocol, score_ids)
        stored = registry.register_benchmark_study_execution(
            payload, actor="methodologist:one",
            identity={
                "issuer": "https://login.example.test/", "subject": "oidc:methodologist:one",
                "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                "authenticated_at": AUTHENTICATED,
            },
            reason="Submit completed study analysis with independent methods review",
        )
        assert stored["status"] == "analysis_reviewed"
        assert stored["fingerprint_verified"] is True
        assert stored["methodologist_id"] == "methodologist:one"
        assert stored["execution"] == payload
        assert registry.list_benchmark_study_executions(protocol["study_id"]) == [stored]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "UPDATE benchmark_study_executions SET reason = 'tampered' WHERE execution_id = ?",
                (payload["execution_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM benchmark_study_executions WHERE execution_id = ?",
                (payload["execution_id"],),
            )


def test_execution_rejects_reviewer_as_methodologist_and_unassigned_score():
    with SQLiteRegistry() as registry:
        artifact, protocol = _registered(registry)
        stored_protocol = registry.register_benchmark_study_protocol(
            protocol, actor="curator:study", reason="Register prespecified study",
        )
        protocol["protocol_fingerprint"] = stored_protocol["protocol_fingerprint"]
        for reviewer_id in ("human:one", "human:two"):
            registry.register_benchmark_principal(
                reviewer_id, display_name=reviewer_id,
                roles=["benchmark_reviewer"], languages=["bn"], principal_kind="human",
                provisioned_by="deployment-operator", reason="Study reviewer",
            )
        registry.register_benchmark_principal(
            "methodologist:one", display_name="Independent Methodologist",
            roles=["benchmark_methodologist"], principal_kind="human",
            provisioned_by="deployment-operator", reason="Independent methods reviewer",
        )
        score_ids = [
            _score(registry, artifact, item_id, reviewer_id)
            for item_id in ("blind:1", "blind:2")
            for reviewer_id in ("human:one", "human:two")
        ]
        payload = _execution(artifact, protocol, score_ids)
        payload["score_records"][0]["evaluator_id"] = "not-assigned"
        with pytest.raises(RegistryIntegrityError, match="outside the protocol"):
            registry.register_benchmark_study_execution(
                payload, actor="methodologist:one",
                identity={
                    "issuer": "https://login.example.test/", "subject": "oidc:methodologist:one",
                    "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                    "authenticated_at": AUTHENTICATED,
                }, reason="Invalid score assignment binding",
            )
