from datetime import datetime, timezone
import sqlite3

import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry
from test_benchmark_study_execution_registry import (
    AUTHENTICATED, _execution, _registered, _score,
)
from test_benchmark_acceptance_review import _review


def _complete_registered_study(registry):
    artifact, protocol = _registered(registry)
    stored_protocol = registry.register_benchmark_study_protocol(
        protocol, actor="curator:study", reason="Register prespecified study",
    )
    protocol["protocol_fingerprint"] = stored_protocol["protocol_fingerprint"]
    for reviewer_id in ("human:one", "human:two"):
        registry.register_benchmark_principal(
            reviewer_id, display_name=reviewer_id, roles=["benchmark_reviewer", "benchmark_acceptance_reviewer"] if reviewer_id == "human:one" else ["benchmark_reviewer"],
            languages=["bn"], principal_kind="human",
            provisioned_by="deployment-operator", reason="Study evaluator",
        )
    registry.register_benchmark_principal(
        "methodologist:one", display_name="Independent Methodologist",
        roles=["benchmark_methodologist", "benchmark_acceptance_reviewer"], principal_kind="human",
        provisioned_by="deployment-operator", reason="Independent analysis review",
    )
    registry.register_benchmark_principal(
        "acceptance:one", display_name="Independent Acceptance Reviewer",
        roles=["benchmark_acceptance_reviewer"], principal_kind="human",
        provisioned_by="deployment-operator", reason="Independent acceptance review",
    )
    score_ids = [
        _score(registry, artifact, item_id, reviewer_id)
        for item_id in ("blind:1", "blind:2")
        for reviewer_id in ("human:one", "human:two")
    ]
    execution_payload = _execution(artifact, protocol, score_ids)
    execution = registry.register_benchmark_study_execution(
        execution_payload, actor="methodologist:one",
        identity={
            "issuer": "https://login.example.test/", "subject": "oidc:methodologist:one",
            "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
            "authenticated_at": AUTHENTICATED,
        },
        reason="Register completed and independently reviewed study",
    )
    return artifact, stored_protocol, execution


def _acceptance_payload(artifact, protocol, execution):
    payload = _review()
    payload.update({
        "study_id": protocol["study_id"],
        "study_version": protocol["study_version"],
        "protocol_fingerprint": protocol["protocol_fingerprint"],
        "execution_id": execution["execution_id"],
        "execution_fingerprint": execution["execution_fingerprint"],
        "benchmark_id": artifact["artifact_id"],
        "benchmark_fingerprint": artifact["content_fingerprint"],
        "rubric_version": protocol["rubric_version"],
        "reviewer_id": "acceptance:one",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    return payload


def test_acceptance_decision_persists_exact_evidence_and_is_append_only():
    with SQLiteRegistry() as registry:
        artifact, protocol, execution = _complete_registered_study(registry)
        payload = _acceptance_payload(artifact, protocol, execution)
        stored = registry.register_benchmark_acceptance_review(
            payload, actor="acceptance:one",
            identity={
                "issuer": "https://login.example.test/", "subject": "oidc:acceptance:one",
                "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                "authenticated_at": AUTHENTICATED,
            },
            reason="Independent three-domain acceptance review",
        )
        assert stored["decision"] == "accepted_for_limited_use"
        assert stored["fingerprint_verified"] is True
        assert stored["protocol_fingerprint"] == protocol["protocol_fingerprint"]
        assert stored["execution_fingerprint"] == execution["execution_fingerprint"]
        assert stored["production_ready"] is False
        assert stored["authorization_granted"] is False
        assert registry.list_benchmark_acceptance_reviews(
            execution_id=execution["execution_id"]
        ) == [stored]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "UPDATE benchmark_acceptance_reviews SET reason='tampered' WHERE review_id=?",
                (payload["review_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM benchmark_acceptance_reviews WHERE review_id=?",
                (payload["review_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            registry.register_benchmark_acceptance_review(
                payload, actor="acceptance:one",
                identity={
                    "issuer": "https://login.example.test/", "subject": "oidc:acceptance:one",
                    "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                    "authenticated_at": AUTHENTICATED,
                },
                reason="Duplicate final decision must fail",
            )


def test_acceptance_review_rejects_blind_evaluator_and_methodologist_conflicts():
    with SQLiteRegistry() as registry:
        artifact, protocol, execution = _complete_registered_study(registry)
        payload = _acceptance_payload(artifact, protocol, execution)
        payload["reviewer_id"] = "human:one"
        with pytest.raises(RegistryIntegrityError, match="blind evaluator"):
            registry.register_benchmark_acceptance_review(
                payload, actor="human:one",
                identity={
                    "issuer": "https://login.example.test/", "subject": "oidc:human:one",
                    "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                    "authenticated_at": AUTHENTICATED,
                }, reason="Blind evaluator must be blocked",
            )
        payload["reviewer_id"] = "methodologist:one"
        with pytest.raises(RegistryIntegrityError, match="methodologist cannot act"):
            registry.register_benchmark_acceptance_review(
                payload, actor="methodologist:one",
                identity={
                    "issuer": "https://login.example.test/", "subject": "oidc:methodologist:one",
                    "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                    "authenticated_at": AUTHENTICATED,
                }, reason="Methodologist must be blocked",
            )


def test_acceptance_review_rejects_stale_execution_binding_and_insufficient_domain_passes():
    with SQLiteRegistry() as registry:
        artifact, protocol, execution = _complete_registered_study(registry)
        payload = _acceptance_payload(artifact, protocol, execution)
        payload["execution_fingerprint"] = "sha256:" + "f" * 64
        with pytest.raises(RegistryIntegrityError, match="exact registered study evidence"):
            registry.register_benchmark_acceptance_review(
                payload, actor="acceptance:one",
                identity={
                    "issuer": "https://login.example.test/", "subject": "oidc:acceptance:one",
                    "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                    "authenticated_at": AUTHENTICATED,
                }, reason="Stale execution must fail",
            )
        payload = _acceptance_payload(artifact, protocol, execution)
        payload["domains"]["operational_security"]["status"] = "needs_revision"
        with pytest.raises(RegistryIntegrityError, match="exact registered study evidence"):
            registry.register_benchmark_acceptance_review(
                payload, actor="acceptance:one",
                identity={
                    "issuer": "https://login.example.test/", "subject": "oidc:acceptance:one",
                    "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                    "authenticated_at": AUTHENTICATED,
                }, reason="Acceptance with a failing domain must fail",
            )
