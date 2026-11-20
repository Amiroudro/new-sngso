import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry


NOW = datetime.now(timezone.utc)
STARTED = NOW - timedelta(minutes=30)
STAMP = STARTED.isoformat()
ACCEPTED = (STARTED + timedelta(minutes=5)).isoformat()
SCORE_AT = (STARTED + timedelta(minutes=10)).isoformat()
COMPLETED = (NOW - timedelta(minutes=10)).isoformat()
AUTHENTICATED = (NOW - timedelta(minutes=1)).isoformat()
REVIEWED = (NOW - timedelta(minutes=5)).isoformat()


def _registered(registry):
    registry.register_benchmark_principal(
        "curator:study", display_name="Study Curator", roles=["benchmark_curator"],
        provisioned_by="deployment-operator", reason="Test curator",
    )
    items = [
        {"item_id": item_id, "task_type": "headline", "input": "পরীক্ষার সংবাদ",
         "expected_output": "পরীক্ষার শিরোনাম", "scoring_criteria": ["Factual accuracy"]}
        for item_id in ("cal:1", "blind:1", "blind:2", "adj:1")
    ]
    artifact = registry.register_benchmark_artifact(
        "artifact:study:bn", language="bn", content={"schema_version": "1.0.0", "items": items},
        actor="curator:study", reason="Register study benchmark",
    )
    protocol = {
        "schema_version": "1.0.0", "study_id": "study:bn:headlines",
        "study_version": 1, "status": "protocol_only",
        "benchmark_id": artifact["artifact_id"],
        "benchmark_fingerprint": artifact["content_fingerprint"],
        "rubric_version": "headline-rubric/2.1",
        "language_task_strata": [{"language": "bn", "task_type": "headline"}],
        "sampling_plan": {
            "sampling_frame_description": "All eligible Bangla headline items in the registered immutable benchmark artifact.",
            "selection_method": "stratified_random",
            "inclusion_criteria": ["Registered items with verified source provenance."],
            "exclusion_criteria": ["Items with unresolved permissions or answer leakage."],
            "target_rationale": "The sample target will be justified by the estimand, precision goal, and planned statistical design."
        },
        "partition_membership": {
            "calibration_item_ids": ["cal:1"],
            "blind_evaluation_item_ids": ["blind:1", "blind:2"],
            "adjudication_item_ids": ["adj:1"]
        },
        "reviewer_plan": [
            {"reviewer_id": "human:one", "principal_kind": "human", "languages": ["bn"],
             "task_types": ["headline"], "training_completed": True,
             "training_record_reference": "training:one",
             "qualification_basis": "Documented professional Bangla editing experience and task-specific assessment."},
            {"reviewer_id": "human:two", "principal_kind": "human", "languages": ["bn"],
             "task_types": ["headline"], "training_completed": True,
             "training_record_reference": "training:two",
             "qualification_basis": "Documented professional Bangla journalism experience and task-specific assessment."}
        ],
        "blind_assignment_plan": [
            {"item_id": item_id, "language": "bn", "task_type": "headline",
             "reviewer_ids": ["human:one", "human:two"]}
            for item_id in ("blind:1", "blind:2")
        ],
        "analysis_plan": {
            "measurement_scale": "ordinal",
            "proposed_statistic": "A statistic suitable for ordinal ratings and crossed evaluator/item design",
            "statistic_rationale": "The selected statistic must reflect ordinal scores, multiple raters, and the actual sampling structure.",
            "unit_of_analysis": "Item-level criterion ratings, with clustering by item and evaluator represented.",
            "confidence_interval_plan": "Use a justified interval method that respects clustering and reports its assumptions and limitations.",
            "missing_data_plan": "Describe missingness, reasons, exclusions, and sensitivity analyses before examining outcomes.",
            "independence_assumptions": "Reviewers score independently without seeing peer scores or adjudication outcomes.",
            "methodological_reviewer": "Independent statistician or research-methods reviewer appointed before data collection."
        },
        "limitations": ["Protocol validation does not establish actual execution, sample adequacy, or reliability."]
    }
    return artifact, protocol


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
        "evaluator_id": evaluator_id, "evaluated_at": SCORE_AT,
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
        "started_at": STAMP, "completed_at": COMPLETED,
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
            "reviewed_at": REVIEWED,
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
