from copy import deepcopy
import sqlite3

import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry


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


def test_study_protocol_registry_persists_and_verifies_exact_protocol():
    with SQLiteRegistry() as registry:
        artifact, protocol = _registered(registry)
        stored = registry.register_benchmark_study_protocol(
            protocol, actor="curator:study", reason="Approve prespecified study design",
        )
        assert stored["fingerprint_verified"] is True
        assert stored["status"] == "protocol_only"
        assert stored["benchmark_fingerprint"] == artifact["content_fingerprint"]
        assert stored["protocol"] == protocol
        assert registry.get_benchmark_study_protocol(protocol["study_id"]) == stored
        assert registry.benchmark_study_protocol_history(protocol["study_id"]) == [stored]
        assert registry.list_benchmark_study_protocols(benchmark_id=artifact["artifact_id"]) == [stored]
        assert registry.schema_version() == 30


def test_study_protocol_versions_are_sequential_immutable_and_audited():
    with SQLiteRegistry() as registry:
        _, protocol = _registered(registry)
        first = registry.register_benchmark_study_protocol(
            protocol, actor="curator:study", reason="Initial protocol",
        )
        second_payload = deepcopy(protocol)
        second_payload["study_version"] = 2
        second_payload["analysis_plan"]["missing_data_plan"] += " Prespecify sensitivity analysis for incomplete rater pairs."
        second = registry.register_benchmark_study_protocol(
            second_payload, actor="curator:study", reason="Clarify missing-data plan before data collection",
        )
        assert second["protocol_fingerprint"] != first["protocol_fingerprint"]
        assert [row["study_version"] for row in registry.benchmark_study_protocol_history(protocol["study_id"])] == [1, 2]
        with pytest.raises(sqlite3.IntegrityError, match="sequential"):
            registry.register_benchmark_study_protocol(
                dict(second_payload, study_version=4), actor="curator:study", reason="Skip version",
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "UPDATE benchmark_study_protocols SET reason = 'tampered' WHERE study_id = ?",
                (protocol["study_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM benchmark_study_protocols WHERE study_id = ?",
                (protocol["study_id"],),
            )
        assert registry.verify_audit_chain() is True


def test_study_protocol_rejects_stale_fingerprint_unknown_items_and_non_curator():
    with SQLiteRegistry() as registry:
        artifact, protocol = _registered(registry)
        with pytest.raises(RegistryIntegrityError, match="fingerprint is stale"):
            registry.register_benchmark_study_protocol(
                dict(protocol, benchmark_fingerprint="sha256:" + "f" * 64),
                actor="curator:study", reason="Stale artifact",
            )
        outside = deepcopy(protocol)
        outside["partition_membership"]["calibration_item_ids"] = ["not-in-artifact"]
        with pytest.raises(RegistryIntegrityError, match="outside the registered benchmark"):
            registry.register_benchmark_study_protocol(
                outside, actor="curator:study", reason="Unknown item",
            )
        registry.register_benchmark_principal(
            "reviewer:study", display_name="Reviewer", roles=["benchmark_reviewer"],
            languages=["bn"], principal_kind="human",
            provisioned_by="deployment-operator", reason="Test reviewer",
        )
        with pytest.raises(RegistryIntegrityError, match="lacks required role"):
            registry.register_benchmark_study_protocol(
                protocol, actor="reviewer:study", reason="Reviewer must not register study",
            )


def test_study_protocol_rejects_item_task_mismatch():
    with SQLiteRegistry() as registry:
        _, protocol = _registered(registry)
        changed = deepcopy(protocol)
        changed["blind_assignment_plan"][0]["task_type"] = "translation"
        changed["language_task_strata"][0]["task_type"] = "translation"
        changed["language_task_strata"].append({"language": "bn", "task_type": "headline"})
        for reviewer in changed["reviewer_plan"]:
            reviewer["task_types"] = ["headline", "translation"]
        with pytest.raises(RegistryIntegrityError, match="task type does not match"):
            registry.register_benchmark_study_protocol(
                changed, actor="curator:study", reason="Mismatched task",
            )
