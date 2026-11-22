"""Bind checkpoint-recovery authorization to a verified OIDC identity.

This adapter composes the existing cryptographic OIDC provider with an
application-owned per-run authorization policy. It never trusts a worker ID
from an unverified request body.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .benchmark_access import AuthenticatedIdentity, BenchmarkIdentityProvider


class WorkerRecoveryAuthorizationError(PermissionError):
    """Worker authentication or per-run authorization failed."""


class OIDCWorkerRecoveryAuthorizer:
    """Callable authorize(worker_id, pipeline_run_id) adapter for recovery.

    request_context_factory must obtain the trusted request/session context for
    the worker, not synthesize credentials from worker_id. authorize_run must
    enforce the application's worker role, run ownership/tenant boundary, and
    any operational approval policy. Both callbacks fail closed.
    """

    def __init__(
        self,
        identity_provider: BenchmarkIdentityProvider,
        *,
        request_context_factory: Callable[[str, str], Any],
        authorize_run: Callable[[AuthenticatedIdentity, str], bool],
        expected_issuer: str,
        expected_audience: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(getattr(identity_provider, "authenticate", None)):
            raise TypeError("identity_provider must implement authenticate")
        if not callable(request_context_factory) or not callable(authorize_run):
            raise TypeError("trusted request-context and run-authorization callbacks are required")
        if not isinstance(expected_issuer, str) or not expected_issuer.startswith("https://"):
            raise ValueError("expected issuer must be HTTPS")
        if not isinstance(expected_audience, str) or not expected_audience.strip():
            raise ValueError("expected audience is required")
        self._identity_provider = identity_provider
        self._request_context_factory = request_context_factory
        self._authorize_run = authorize_run
        self._expected_issuer = expected_issuer
        self._expected_audience = expected_audience
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(self, worker_id: str, pipeline_run_id: str) -> bool:
        if not isinstance(worker_id, str) or not worker_id.strip():
            return False
        if not isinstance(pipeline_run_id, str) or not pipeline_run_id.strip():
            return False
        try:
            request_context = self._request_context_factory(worker_id, pipeline_run_id)
            identity = self._identity_provider.authenticate(request_context)
            if not isinstance(identity, AuthenticatedIdentity):
                return False
            if identity.principal_id != worker_id:
                return False
            if identity.issuer != self._expected_issuer or identity.audience != self._expected_audience:
                return False
            if not identity.subject.strip() or not identity.session_id.strip():
                return False
            if identity.authenticated_at.tzinfo is None or identity.expires_at.tzinfo is None:
                return False
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                return False
            if identity.authenticated_at > now or identity.expires_at <= now:
                return False
            return self._authorize_run(identity, pipeline_run_id) is True
        except Exception:
            # Never leak credentials/provider errors into worker control flow.
            return False
