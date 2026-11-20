"""Trusted-session boundary for benchmark registry operations.

The identity provider is an infrastructure adapter. It must validate the
actual login/session (signature or server-side session lookup), issuer,
audience, expiry, and revocation before returning an identity. This module
never accepts a principal ID supplied by an untrusted request body.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from .registry import RegistryIntegrityError


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Identity claims returned only after provider-side authentication."""

    principal_id: str
    issuer: str
    audience: str
    authenticated_at: datetime
    expires_at: datetime
    session_id: str


@runtime_checkable
class BenchmarkIdentityProvider(Protocol):
    """Adapter contract for a trusted OIDC or server-side session provider."""

    def authenticate(self, request_context: Any) -> AuthenticatedIdentity:
        """Verify the session and return claims, or raise on any failure."""
        ...


class AuthenticatedBenchmarkAccess:
    """Role-checking facade for application code handling untrusted requests.

    Production handlers should expose this facade, not raw registry methods.
    The identity provider must be configured with the exact issuer/audience
    and perform cryptographic/session validation, including session revocation.
    """

    def __init__(
        self,
        registry: Any,
        identity_provider: BenchmarkIdentityProvider,
        *,
        expected_issuer: str,
        expected_audience: str,
        clock: Any = None,
    ) -> None:
        if not expected_issuer.strip() or not expected_audience.strip():
            raise ValueError("expected issuer and audience are required")
        self._registry = registry
        self._identity_provider = identity_provider
        self._expected_issuer = expected_issuer
        self._expected_audience = expected_audience
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _principal(self, request_context: Any, required_role: str) -> str:
        identity = self._identity_provider.authenticate(request_context)
        if not isinstance(identity, AuthenticatedIdentity):
            raise RegistryIntegrityError("identity provider returned an invalid identity")
        if not identity.principal_id.strip() or not identity.session_id.strip():
            raise RegistryIntegrityError("authenticated identity is incomplete")
        if identity.issuer != self._expected_issuer:
            raise RegistryIntegrityError("authenticated identity issuer mismatch")
        if identity.audience != self._expected_audience:
            raise RegistryIntegrityError("authenticated identity audience mismatch")
        now = self._clock()
        if identity.authenticated_at.tzinfo is None or identity.expires_at.tzinfo is None:
            raise RegistryIntegrityError("identity timestamps must be timezone-aware")
        if identity.authenticated_at > now or identity.expires_at <= now:
            raise RegistryIntegrityError("authenticated session is not currently valid")
        principal = self._registry.get_benchmark_principal(identity.principal_id)
        if principal is None or not principal["active"]:
            raise RegistryIntegrityError("authenticated principal is not active in the benchmark registry")
        if required_role not in principal["roles"]:
            raise RegistryIntegrityError(f"authenticated principal lacks required role: {required_role}")
        return identity.principal_id

    def register_artifact(
        self, request_context: Any, artifact_id: str, *, language: str,
        content: Any, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_curator")
        return self._registry.register_benchmark_artifact(
            artifact_id, language=language, content=content, actor=actor, reason=reason
        )

    def submit_review(
        self, request_context: Any, review: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_reviewer")
        if review.get("reviewer", {}).get("reviewer_id") != actor:
            raise RegistryIntegrityError("authenticated reviewer must match the review declaration")
        return self._registry.add_benchmark_review(review, actor=actor, reason=reason)

    def adjudicate(
        self, request_context: Any, payload: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_adjudicator")
        if payload.get("adjudicator", {}).get("principal_id") != actor:
            raise RegistryIntegrityError("authenticated adjudicator must match the adjudication declaration")
        return self._registry.add_benchmark_adjudication(payload, actor=actor, reason=reason)
