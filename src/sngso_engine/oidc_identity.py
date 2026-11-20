"""Cryptographic OIDC JWT verification adapter for benchmark identity.

Network access and JWKS retrieval are deliberately injected. Deployments must
pin the issuer's HTTPS JWKS endpoint in a trusted resolver; no token or request
field can choose a URL, issuer, key source, or accepted algorithm.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable, Protocol

import jwt

from .benchmark_access import AuthenticatedIdentity
from .registry import RegistryIntegrityError


class OIDCSigningKeyResolver(Protocol):
    def resolve_signing_key(self, *, key_id: str, algorithm: str) -> Any:
        """Return a public verification key from the configured issuer JWKS."""
        ...


class OIDCSessionRevocationChecker(Protocol):
    def is_revoked(self, *, issuer: str, subject: str, session_id: str, token_id: str) -> bool:
        """Check provider logout/session revocation; errors must fail closed."""
        ...


@dataclass(frozen=True)
class OIDCProviderConfig:
    issuer: str
    audience: str
    adapter_id: str
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    required_amr: tuple[str, ...] = ("mfa",)
    required_acr: str | None = None
    max_authentication_age_seconds: int = 900
    clock_skew_seconds: int = 30
    max_token_length: int = 16_384

    def __post_init__(self) -> None:
        if not self.issuer.startswith("https://") or not self.audience.strip() or not self.adapter_id.strip():
            raise ValueError("OIDC issuer must be HTTPS and audience/adapter ID must be configured")
        if not self.allowed_algorithms or any(alg not in {"RS256", "PS256", "ES256"} for alg in self.allowed_algorithms):
            raise ValueError("OIDC algorithms must use an explicit asymmetric allowlist")
        if self.max_authentication_age_seconds < 1 or self.clock_skew_seconds < 0:
            raise ValueError("OIDC time policy is invalid")
        if self.max_token_length < 1024:
            raise ValueError("OIDC token size limit is too small")


class OIDCIdentityProvider:
    """Verify signed OIDC JWTs against pinned infrastructure and map subjects.

    The key resolver must only use the configured issuer's trusted JWKS URL,
    enforce HTTPS, validate key use/algorithm, and handle rotation. The
    revocation checker must query a trusted server-side session/revocation
    source. Both are mandatory and failures deny authentication.
    """

    def __init__(
        self,
        config: OIDCProviderConfig,
        *,
        token_extractor: Callable[[Any], str],
        key_resolver: OIDCSigningKeyResolver,
        revocation_checker: OIDCSessionRevocationChecker,
        subject_to_principal: Callable[[str, str], str | None],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(token_extractor) or not callable(subject_to_principal):
            raise ValueError("trusted token extraction and subject mapping are required")
        if not callable(getattr(key_resolver, "resolve_signing_key", None)):
            raise ValueError("a trusted OIDC signing-key resolver is required")
        if not callable(getattr(revocation_checker, "is_revoked", None)):
            raise ValueError("a trusted session revocation checker is required")
        self.config = config
        self._token_extractor = token_extractor
        self._key_resolver = key_resolver
        self._revocation_checker = revocation_checker
        self._subject_to_principal = subject_to_principal
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authenticate(self, request_context: Any) -> AuthenticatedIdentity:
        try:
            token = self._token_extractor(request_context)
            if not isinstance(token, str) or not token or len(token) > self.config.max_token_length:
                raise RegistryIntegrityError("OIDC bearer token is missing or exceeds the configured size limit")
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            algorithm = header.get("alg")
            if not isinstance(key_id, str) or not key_id.strip():
                raise RegistryIntegrityError("OIDC token has no signing key identifier")
            if algorithm not in self.config.allowed_algorithms:
                raise RegistryIntegrityError("OIDC token algorithm is not allowed")
            if header.get("crit"):
                raise RegistryIntegrityError("OIDC token uses unsupported critical headers")
            key = self._key_resolver.resolve_signing_key(key_id=key_id, algorithm=algorithm)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.config.allowed_algorithms),
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=self.config.clock_skew_seconds,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "auth_time", "jti", "sid"],
                         "verify_exp": False, "verify_iat": False, "verify_nbf": False},
            )
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise RegistryIntegrityError("OIDC verifier clock must be timezone-aware")
            subject = claims.get("sub")
            token_id = claims.get("jti")
            session_id = claims.get("sid")
            auth_time = claims.get("auth_time")
            expires_at = claims.get("exp")
            audiences = claims.get("aud")
            if not isinstance(subject, str) or not subject.strip():
                raise RegistryIntegrityError("OIDC subject is missing")
            if not isinstance(token_id, str) or not token_id.strip() or not isinstance(session_id, str) or not session_id.strip():
                raise RegistryIntegrityError("OIDC token or session identifier is invalid")
            if isinstance(audiences, str):
                audience_values = [audiences]
            elif isinstance(audiences, list) and all(isinstance(item, str) for item in audiences):
                audience_values = audiences
            else:
                raise RegistryIntegrityError("OIDC audience claim is malformed")
            if len(audience_values) > 1 and claims.get("azp") != self.config.audience:
                raise RegistryIntegrityError("OIDC authorized-party claim is missing or mismatched")
            if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)) or not math.isfinite(auth_time):
                raise RegistryIntegrityError("OIDC authentication time is invalid")
            issued_at = claims.get("iat")
            if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)) or not math.isfinite(issued_at):
                raise RegistryIntegrityError("OIDC issued-at claim is invalid")
            if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
                raise RegistryIntegrityError("OIDC expiry claim is invalid")
            now_epoch = now.timestamp()
            if expires_at <= now_epoch - self.config.clock_skew_seconds:
                raise RegistryIntegrityError("OIDC token is expired")
            if issued_at > now_epoch + self.config.clock_skew_seconds:
                raise RegistryIntegrityError("OIDC issued-at claim is in the future")
            not_before = claims.get("nbf")
            if not_before is not None and (
                isinstance(not_before, bool)
                or not isinstance(not_before, (int, float))
                or not math.isfinite(not_before)
                or not_before > now_epoch + self.config.clock_skew_seconds
            ):
                raise RegistryIntegrityError("OIDC not-before claim is invalid or in the future")
            authenticated_at = datetime.fromtimestamp(auth_time, timezone.utc)
            expiry = datetime.fromtimestamp(expires_at, timezone.utc)
            age = (now - authenticated_at).total_seconds()
            if age < -self.config.clock_skew_seconds or age > self.config.max_authentication_age_seconds:
                raise RegistryIntegrityError("OIDC authentication is outside the permitted freshness window")
            methods = claims.get("amr", [])
            if not isinstance(methods, list) or not all(isinstance(item, str) for item in methods):
                raise RegistryIntegrityError("OIDC authentication-method claim is malformed")
            if not set(self.config.required_amr).issubset(set(methods)):
                raise RegistryIntegrityError("OIDC authentication methods do not meet policy")
            context = claims.get("acr", "")
            if not isinstance(context, str) or not context.strip():
                raise RegistryIntegrityError("OIDC authentication context is missing")
            if self.config.required_acr and context != self.config.required_acr:
                raise RegistryIntegrityError("OIDC authentication context does not meet policy")
            principal_id = self._subject_to_principal(self.config.issuer, subject)
            if not isinstance(principal_id, str) or not principal_id.strip():
                raise RegistryIntegrityError("OIDC issuer/subject is not mapped to an active application identity")
            revoked = self._revocation_checker.is_revoked(
                issuer=self.config.issuer, subject=subject, session_id=session_id, token_id=token_id
            )
            if revoked is not False:
                raise RegistryIntegrityError("OIDC session or token is revoked or revocation status is unavailable")
            return AuthenticatedIdentity(
                principal_id=principal_id,
                issuer=self.config.issuer,
                audience=self.config.audience,
                authenticated_at=authenticated_at,
                expires_at=expiry,
                session_id=session_id,
                subject=subject,
                authentication_methods=tuple(methods),
                authentication_context=context,
                provider_key_id=key_id,
                provider_adapter=self.config.adapter_id,
            )
        except RegistryIntegrityError:
            raise
        except Exception as exc:
            # Do not expose token contents, key material, or provider internals.
            raise RegistryIntegrityError("OIDC authentication failed closed") from exc
