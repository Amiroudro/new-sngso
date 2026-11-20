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
