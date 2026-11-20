from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.benchmark_access import (
    AuthenticatedBenchmarkAccess,
    AuthenticatedIdentity,
)
from sngso_engine.registry import RegistryIntegrityError


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class FakeIdentityProvider:
    def __init__(self, identity):
        self.identity = identity

    def authenticate(self, request_context):
        if request_context != "trusted-request":
            raise RegistryIntegrityError("untrusted request context")
        return self.identity


class FakeRegistry:
    def __init__(self, *, roles=("benchmark_curator",), active=True):
        self.principal = {
            "principal_id": "person:42",
            "roles": list(roles),
            "languages": ["en", "bn"],
            "active": active,
        }
        self.calls = []

    def get_benchmark_principal(self, principal_id):
        return self.principal if principal_id == self.principal["principal_id"] else None

    def register_benchmark_artifact(self, artifact_id, **kwargs):
        self.calls.append(("artifact", artifact_id, kwargs))
        return {"artifact_id": artifact_id, **kwargs}

    def add_benchmark_review(self, review, **kwargs):
        self.calls.append(("review", review, kwargs))
        return review

    def add_benchmark_adjudication(self, payload, **kwargs):
        self.calls.append(("adjudication", payload, kwargs))
        return payload

    def create_benchmark_review_assignment(self, payload, **kwargs):
        self.calls.append(("assignment", payload, kwargs))
        return payload

    def get_benchmark_review_assignment(self, assignment_id):
        return {"assignment_id": assignment_id, "assigned_by": "person:42", "reviewer_id": "person:42"}

    def transition_benchmark_review_assignment(self, assignment_id, **kwargs):
        self.calls.append(("assignment_transition", assignment_id, kwargs))
        return {"assignment_id": assignment_id, **kwargs}

    def list_benchmark_review_assignments(self, **kwargs):
        self.calls.append(("assignment_queue", kwargs))
        return [{"reviewer_id": kwargs.get("reviewer_id"), "status": kwargs.get("status")}]


def identity(**overrides):
    values = {
        "principal_id": "person:42",
        "issuer": "https://login.example.test/",
        "audience": "sngso-benchmark",
        "authenticated_at": NOW - timedelta(minutes=2),
        "expires_at": NOW + timedelta(minutes=10),
        "session_id": "session:opaque",
    }
    values.update(overrides)
    return AuthenticatedIdentity(**values)


def access(registry=None, session=None):
    return AuthenticatedBenchmarkAccess(
        registry or FakeRegistry(),
        FakeIdentityProvider(session or identity()),
        expected_issuer="https://login.example.test/",
        expected_audience="sngso-benchmark",
        clock=lambda: NOW,
    )


def test_trusted_session_can_register_artifact_without_client_supplied_actor():
    registry = FakeRegistry()
    result = access(registry).register_artifact(
        "trusted-request", "bn-editorial-001", language="bn",
        content={"items": []}, reason="Curated initial benchmark",
    )
    assert result["artifact_id"] == "bn-editorial-001"
    assert registry.calls[0][2]["actor"] == "person:42"


@pytest.mark.parametrize(
    "claims, message",
    [
        ({"issuer": "https://attacker.example/"}, "issuer mismatch"),
        ({"audience": "other-app"}, "audience mismatch"),
        ({"expires_at": NOW}, "not currently valid"),
        ({"authenticated_at": NOW + timedelta(seconds=1)}, "not currently valid"),
    ],
)
def test_invalid_or_expired_identity_is_rejected(claims, message):
    with pytest.raises(RegistryIntegrityError, match=message):
        access(session=identity(**claims)).register_artifact(
            "trusted-request", "artifact", language="en",
            content={"items": []}, reason="test",
        )


def test_inactive_registry_principal_is_rejected_even_with_valid_login():
    registry = FakeRegistry(active=False)
    with pytest.raises(RegistryIntegrityError, match="not active"):
        access(registry).register_artifact(
            "trusted-request", "artifact", language="en",
            content={"items": []}, reason="test",
        )


def test_missing_registry_role_is_rejected():
    registry = FakeRegistry(roles=("benchmark_reviewer",))
    with pytest.raises(RegistryIntegrityError, match="lacks required role"):
        access(registry).register_artifact(
            "trusted-request", "artifact", language="en",
            content={"items": []}, reason="test",
        )


def test_untrusted_request_context_is_rejected_by_provider():
    with pytest.raises(RegistryIntegrityError, match="untrusted request"):
        access().register_artifact(
            "client-supplied-id", "artifact", language="en",
            content={"items": []}, reason="test",
        )


def test_review_identity_must_match_authenticated_principal():
    registry = FakeRegistry(roles=("benchmark_reviewer",))
    review = {"reviewer": {"reviewer_id": "person:attacker"}}
    with pytest.raises(RegistryIntegrityError, match="must match"):
        access(registry).submit_review("trusted-request", review, reason="test")
    assert registry.calls == []


def test_adjudicator_identity_must_match_authenticated_principal():
    registry = FakeRegistry(roles=("benchmark_adjudicator",))
    payload = {"adjudicator": {"principal_id": "person:attacker"}}
    with pytest.raises(RegistryIntegrityError, match="must match"):
        access(registry).adjudicate("trusted-request", payload, reason="test")
    assert registry.calls == []


def test_assignment_actor_is_derived_from_authenticated_curator():
    registry = FakeRegistry()
    payload = {"assigned_by": "person:42", "reviewer_id": "person:reviewer"}
    access(registry).assign_review("trusted-request", payload, reason="Assign review")
    assert registry.calls[-1][0] == "assignment"
    assert registry.calls[-1][2]["actor"] == "person:42"


def test_assignment_cannot_claim_another_assigner():
    registry = FakeRegistry()
    with pytest.raises(RegistryIntegrityError, match="must match"):
        access(registry).assign_review(
            "trusted-request", {"assigned_by": "person:attacker"},
            reason="Attempted actor substitution",
        )
    assert registry.calls == []


def test_acceptance_uses_authenticated_reviewer_and_not_request_actor():
    registry = FakeRegistry(roles=("benchmark_reviewer",))
    result = access(registry).transition_review_assignment(
        "trusted-request", "assignment:001", event="accept",
        at="2026-11-20T12:00:00Z", reason="Accepted",
    )
    assert result["assignment_id"] == "assignment:001"
    assert registry.calls[-1][2]["actor"] == "person:42"


def test_assignment_expiry_requires_authenticated_curator():
    registry = FakeRegistry(roles=("benchmark_curator",))
    access(registry).transition_review_assignment(
        "trusted-request", "assignment:001", event="expire",
        at="2026-11-21T12:00:00Z", reason="Deadline passed",
    )
    assert registry.calls[-1][2]["actor"] == "person:42"


def test_reviewer_queue_is_scoped_to_authenticated_principal():
    registry = FakeRegistry(roles=("benchmark_reviewer",))
    result = access(registry).reviewer_assignment_queue(
        "trusted-request", status="assigned", language="en",
    )
    assert result[0]["reviewer_id"] == "person:42"
    assert registry.calls[-1][1]["reviewer_id"] == "person:42"


def test_reviewer_queue_rejects_unqualified_language():
    registry = FakeRegistry(roles=("benchmark_reviewer",))
    registry.principal["languages"] = ["bn"]
    with pytest.raises(RegistryIntegrityError, match="not qualified"):
        access(registry).reviewer_assignment_queue(
            "trusted-request", language="en",
        )


def test_assignment_transition_rejects_authenticated_non_assignee():
    registry = FakeRegistry(roles=("benchmark_reviewer",))
    registry.get_benchmark_review_assignment = lambda assignment_id: {
        "assignment_id": assignment_id,
        "assigned_by": "person:curator",
        "reviewer_id": "person:someone-else",
    }
    with pytest.raises(RegistryIntegrityError, match="must match the assigned reviewer"):
        access(registry).transition_review_assignment(
            "trusted-request", "assignment:001", event="accept",
            at="2026-11-20T12:00:00Z", reason="wrong reviewer",
        )
    assert not any(call[0] == "assignment_transition" for call in registry.calls)
