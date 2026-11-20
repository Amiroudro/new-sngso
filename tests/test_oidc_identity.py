from datetime import datetime, timezone
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from sngso_engine.benchmark_access import AuthenticatedIdentity
from sngso_engine.oidc_identity import OIDCIdentityProvider, OIDCProviderConfig
from sngso_engine.registry import RegistryIntegrityError


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ISSUER = "https://login.example.test/"
AUDIENCE = "sngso-benchmark"


class KeyResolver:
    def __init__(self, keys):
        self.keys = keys
        self.calls = []

    def resolve_signing_key(self, *, key_id, algorithm):
        self.calls.append((key_id, algorithm))
        if key_id not in self.keys:
            raise KeyError("unknown key")
        return self.keys[key_id]


class RevocationChecker:
    def __init__(self, result=False):
        self.result = result
        self.calls = []

    def is_revoked(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_provider(signing_key, *, revocation=False, keys=None, config=None, mapper=None):
    resolver = KeyResolver(keys or {"key-1": signing_key.public_key()})
    revoker = RevocationChecker(revocation)
    provider = OIDCIdentityProvider(
        config or OIDCProviderConfig(
            issuer=ISSUER, audience=AUDIENCE, adapter_id="sngso-oidc/1",
            required_amr=("mfa",), required_acr="urn:example:loa:2",
        ),
        token_extractor=lambda request: request,
        key_resolver=resolver,
        revocation_checker=revoker,
        subject_to_principal=mapper or (lambda issuer, subject: "person:42" if (issuer, subject) == (ISSUER, "subject-42") else None),
        clock=lambda: NOW,
    )
    return provider, resolver, revoker


def make_token(key, **overrides):
    claims = {
        "iss": ISSUER,
        "sub": "subject-42",
        "aud": AUDIENCE,
        "exp": int(NOW.timestamp()) + 600,
        "iat": int(NOW.timestamp()) - 120,
        "auth_time": int(NOW.timestamp()) - 120,
        "jti": "token-001",
        "sid": "session-001",
        "amr": ["pwd", "mfa"],
        "acr": "urn:example:loa:2",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1", "typ": "JWT"})


def test_valid_signed_oidc_token_returns_mapped_authenticated_identity(signing_key):
    provider, resolver, revoker = make_provider(signing_key)
    identity = provider.authenticate(make_token(signing_key))
    assert isinstance(identity, AuthenticatedIdentity)
    assert identity.principal_id == "person:42"
    assert identity.subject == "subject-42"
    assert identity.issuer == ISSUER
    assert identity.audience == AUDIENCE
    assert identity.provider_key_id == "key-1"
    assert identity.authentication_methods == ("pwd", "mfa")
    assert identity.session_id == "session-001"
    assert resolver.calls == [("key-1", "RS256")]
    assert revoker.calls[0]["token_id"] == "token-001"


def test_invalid_signature_is_rejected(signing_key):
    provider, _, _ = make_provider(signing_key)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(RegistryIntegrityError, match="failed closed"):
        provider.authenticate(make_token(other))


def test_unknown_key_id_is_rejected(signing_key):
    provider, _, _ = make_provider(signing_key)
    token = jwt.encode(
        {"iss": ISSUER, "sub": "subject-42", "aud": AUDIENCE,
         "exp": int(NOW.timestamp()) + 600, "iat": int(NOW.timestamp()),
         "auth_time": int(NOW.timestamp()), "jti": "j", "sid": "s",
         "amr": ["mfa"], "acr": "urn:example:loa:2"},
        signing_key, algorithm="RS256", headers={"kid": "unknown"},
    )
    with pytest.raises(RegistryIntegrityError, match="failed closed"):
        provider.authenticate(token)


def test_unapproved_algorithm_is_rejected_before_key_lookup(signing_key):
    provider, resolver, _ = make_provider(signing_key)
    token = jwt.encode(
        {"iss": ISSUER, "sub": "subject-42", "aud": AUDIENCE,
         "exp": int(NOW.timestamp()) + 600},
        "s" * 32, algorithm="HS256", headers={"kid": "key-1"},
    )
    with pytest.raises(RegistryIntegrityError, match="algorithm is not allowed"):
        provider.authenticate(token)
    assert resolver.calls == []


@pytest.mark.parametrize(
    "claims, message",
    [
        ({"iss": "https://attacker.example/"}, "failed closed"),
        ({"aud": "another-client"}, "failed closed"),
        ({"exp": int(NOW.timestamp()) - 60}, "token is expired"),
        ({"iat": int(NOW.timestamp()) + 60}, "issued-at claim is in the future"),
        ({"nbf": int(NOW.timestamp()) + 60}, "not-before claim is invalid or in the future"),
        ({"sub": ""}, "subject is missing"),
        ({"sid": ""}, "identifier is invalid"),
        ({"amr": ["pwd"]}, "methods do not meet policy"),
        ({"acr": "urn:weak"}, "context does not meet policy"),
        ({"auth_time": int(NOW.timestamp()) - 1200}, "freshness window"),
    ],
)
def test_invalid_oidc_claims_fail_closed(signing_key, claims, message):
    provider, _, _ = make_provider(signing_key)
    with pytest.raises(RegistryIntegrityError, match=message):
        provider.authenticate(make_token(signing_key, **claims))


def test_unmapped_subject_is_rejected(signing_key):
    provider, _, _ = make_provider(signing_key, mapper=lambda issuer, subject: None)
    with pytest.raises(RegistryIntegrityError, match="not mapped"):
        provider.authenticate(make_token(signing_key))


def test_revoked_session_is_rejected(signing_key):
    provider, _, _ = make_provider(signing_key, revocation=True)
    with pytest.raises(RegistryIntegrityError, match="revoked"):
        provider.authenticate(make_token(signing_key))


def test_unavailable_revocation_status_fails_closed(signing_key):
    provider, _, revoker = make_provider(signing_key)
    revoker.is_revoked = lambda **kwargs: None
    with pytest.raises(RegistryIntegrityError, match="revoked"):
        provider.authenticate(make_token(signing_key))


def test_key_rotation_uses_current_resolver_key(signing_key):
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider, resolver, _ = make_provider(
        signing_key, keys={"key-2": rotated.public_key()}
    )
    token = jwt.encode(
        {"iss": ISSUER, "sub": "subject-42", "aud": AUDIENCE,
         "exp": int(NOW.timestamp()) + 600, "iat": int(NOW.timestamp()) - 60,
         "auth_time": int(NOW.timestamp()) - 60, "jti": "token-2",
         "sid": "session-2", "amr": ["mfa"], "acr": "urn:example:loa:2"},
        rotated, algorithm="RS256", headers={"kid": "key-2"},
    )
    assert provider.authenticate(token).provider_key_id == "key-2"
    assert resolver.calls == [("key-2", "RS256")]


def test_multi_audience_requires_matching_authorized_party(signing_key):
    provider, _, _ = make_provider(signing_key)
    token = make_token(signing_key, aud=[AUDIENCE, "other-client"])
    with pytest.raises(RegistryIntegrityError, match="authorized-party"):
        provider.authenticate(token)


def test_token_extraction_and_malformed_tokens_fail_closed(signing_key):
    provider, _, _ = make_provider(signing_key)
    with pytest.raises(RegistryIntegrityError, match="failed closed"):
        provider.authenticate("not-a-jwt")
    with pytest.raises(RegistryIntegrityError, match="missing or exceeds"):
        provider.authenticate("")
