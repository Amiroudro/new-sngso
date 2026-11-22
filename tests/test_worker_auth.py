from datetime import datetime, timedelta, timezone

from sngso_engine.benchmark_access import AuthenticatedIdentity
from sngso_engine.worker_auth import OIDCWorkerRecoveryAuthorizer


NOW = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)
ISSUER = "https://identity.example.test/"
AUDIENCE = "sngso-worker"


def identity(**changes):
    values = {
        "principal_id": "worker:42",
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "authenticated_at": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(minutes=5),
        "session_id": "session-42",
        "subject": "subject-42",
        "authentication_methods": ("mfa",),
        "authentication_context": "urn:loa:2",
    }
    values.update(changes)
    return AuthenticatedIdentity(**values)


class IdentityProvider:
    def __init__(self, value=None, error=None):
        self.value = value or identity()
        self.error = error
        self.calls = []

    def authenticate(self, context):
        self.calls.append(context)
        if self.error:
            raise self.error
        return self.value


def make_authorizer(provider=None, **kwargs):
    provider = provider or IdentityProvider()
    contexts = []
    policies = []
    authorizer = OIDCWorkerRecoveryAuthorizer(
        provider,
        request_context_factory=lambda worker, run: contexts.append((worker, run)) or {"trusted": "session"},
        authorize_run=lambda authenticated, run: policies.append((authenticated.principal_id, run)) or True,
        expected_issuer=ISSUER,
        expected_audience=AUDIENCE,
        clock=lambda: NOW,
        **kwargs,
    )
    return authorizer, provider, contexts, policies


def test_oidc_worker_authorizer_binds_verified_identity_to_requested_worker_and_run():
    authorizer, provider, contexts, policies = make_authorizer()
    assert authorizer("worker:42", "pipeline-run:42") is True
    assert contexts == [("worker:42", "pipeline-run:42")]
    assert provider.calls == [{"trusted": "session"}]
    assert policies == [("worker:42", "pipeline-run:42")]


def test_oidc_worker_authorizer_rejects_mismatched_worker_without_policy_call():
    authorizer, _, _, policies = make_authorizer()
    assert authorizer("worker:attacker", "pipeline-run:42") is False
    assert policies == []


def test_oidc_worker_authorizer_rejects_wrong_issuer_or_audience():
    for changes in ({"issuer": "https://attacker.test/"}, {"audience": "other"}):
        authorizer, _, _, policies = make_authorizer(IdentityProvider(identity(**changes)))
        assert authorizer("worker:42", "pipeline-run:42") is False
        assert policies == []


def test_oidc_worker_authorizer_rejects_expired_or_future_authenticated_identity():
    for changes in (
        {"expires_at": NOW},
        {"authenticated_at": NOW + timedelta(seconds=1)},
    ):
        authorizer, _, _, policies = make_authorizer(IdentityProvider(identity(**changes)))
        assert authorizer("worker:42", "pipeline-run:42") is False
        assert policies == []


def test_oidc_worker_authorizer_rejects_incomplete_identity():
    for changes in ({"subject": ""}, {"session_id": ""}):
        authorizer, _, _, policies = make_authorizer(IdentityProvider(identity(**changes)))
        assert authorizer("worker:42", "pipeline-run:42") is False
        assert policies == []


def test_oidc_worker_authorizer_fails_closed_on_identity_provider_error():
    authorizer, _, _, policies = make_authorizer(IdentityProvider(error=RuntimeError("secret")))
    assert authorizer("worker:42", "pipeline-run:42") is False
    assert policies == []


def test_oidc_worker_authorizer_requires_literal_true_from_run_policy():
    provider = IdentityProvider()
    authorizer, _, _, _ = make_authorizer(provider)
    # Rebuild with a policy returning a truthy non-boolean value.
    authorizer = OIDCWorkerRecoveryAuthorizer(
        provider,
        request_context_factory=lambda worker, run: object(),
        authorize_run=lambda authenticated, run: 1,
        expected_issuer=ISSUER,
        expected_audience=AUDIENCE,
        clock=lambda: NOW,
    )
    assert authorizer("worker:42", "pipeline-run:42") is False


def test_oidc_worker_authorizer_rejects_invalid_request_identifiers():
    authorizer, provider, _, _ = make_authorizer()
    assert authorizer("", "pipeline-run:42") is False
    assert authorizer("worker:42", "") is False
    assert provider.calls == []
