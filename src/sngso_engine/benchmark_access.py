"""Trusted-session boundary for benchmark registry operations.

The identity provider is an infrastructure adapter. It must validate the
actual login/session (signature or server-side session lookup), issuer,
audience, expiry, and revocation before returning an identity. This module
never accepts a principal ID supplied by an untrusted request body.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol, runtime_checkable

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
    subject: str = ""
    authentication_methods: tuple[str, ...] = ()
    authentication_context: str = ""
    provider_key_id: str = ""
    provider_adapter: str = ""


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
        subject_to_principal: Callable[[str, str], str | None],
        clock: Any = None,
    ) -> None:
        if not expected_issuer.strip() or not expected_audience.strip():
            raise ValueError("expected issuer and audience are required")
        self._registry = registry
        self._identity_provider = identity_provider
        self._expected_issuer = expected_issuer
        self._expected_audience = expected_audience
        if not callable(subject_to_principal):
            raise ValueError("a trusted issuer-and-subject mapping is required")
        self._subject_to_principal = subject_to_principal
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _identity(self, request_context: Any) -> AuthenticatedIdentity:
        identity = self._identity_provider.authenticate(request_context)
        if not isinstance(identity, AuthenticatedIdentity):
            raise RegistryIntegrityError("identity provider returned an invalid identity")
        if not identity.principal_id.strip() or not identity.session_id.strip():
            raise RegistryIntegrityError("authenticated identity is incomplete")
        if identity.issuer != self._expected_issuer:
            raise RegistryIntegrityError("authenticated identity issuer mismatch")
        if identity.audience != self._expected_audience:
            raise RegistryIntegrityError("authenticated identity audience mismatch")
        if not identity.subject.strip():
            raise RegistryIntegrityError("authenticated identity subject is missing")
        mapped_principal = self._subject_to_principal(identity.issuer, identity.subject)
        if mapped_principal != identity.principal_id:
            raise RegistryIntegrityError("provider issuer/subject is not bound to the authenticated registry principal")
        now = self._clock()
        if identity.authenticated_at.tzinfo is None or identity.expires_at.tzinfo is None:
            raise RegistryIntegrityError("identity timestamps must be timezone-aware")
        if identity.authenticated_at > now or identity.expires_at <= now:
            raise RegistryIntegrityError("authenticated session is not currently valid")
        return identity

    def _principal(self, request_context: Any, required_role: str | tuple[str, ...]) -> str:
        identity = self._identity(request_context)
        principal = self._registry.get_benchmark_principal(identity.principal_id)
        if principal is None or not principal["active"]:
            raise RegistryIntegrityError("authenticated principal is not active in the benchmark registry")
        allowed_roles = (required_role,) if isinstance(required_role, str) else required_role
        if not any(role in principal["roles"] for role in allowed_roles):
            raise RegistryIntegrityError(f"authenticated principal lacks required role: {allowed_roles}")
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
        identity = self._identity(request_context)
        actor = identity.principal_id
        principal = self._registry.get_benchmark_principal(actor)
        if principal is None or not principal["active"] or "benchmark_reviewer" not in principal["roles"]:
            raise RegistryIntegrityError("authenticated principal lacks required role: ('benchmark_reviewer',)")
        if principal.get("principal_kind") != "human":
            raise RegistryIntegrityError("human reviewer identity attestation requires a human-classified principal")
        if self._clock() - identity.authenticated_at > timedelta(minutes=15):
            raise RegistryIntegrityError("review submission requires recent authentication within 15 minutes")
        if review.get("reviewer", {}).get("reviewer_id") != actor:
            raise RegistryIntegrityError("authenticated reviewer must match the review declaration")
        if not identity.subject.strip() or not identity.authentication_methods or not identity.authentication_context.strip():
            raise RegistryIntegrityError("identity provider must supply subject, authentication methods, and authentication context for review attestation")
        if not identity.provider_key_id.strip() or not identity.provider_adapter.strip():
            raise RegistryIntegrityError("identity provider must identify its verification key and adapter")
        result = self._registry.add_benchmark_review(review, actor=actor, reason=reason)
        attestation = {
            "attestation_id": f"identity-attestation:{review['review_id']}:{result['revision']}",
            "review_id": review["review_id"], "review_revision": result["revision"],
            "principal_id": actor, "issuer": identity.issuer, "audience": identity.audience,
            "subject": identity.subject,
            "authenticated_at": identity.authenticated_at.isoformat(),
            "expires_at": identity.expires_at.isoformat(),
            "authentication_methods": list(identity.authentication_methods),
            "authentication_context": identity.authentication_context,
            "provider_key_id": identity.provider_key_id,
            "provider_adapter": identity.provider_adapter,
            "session_fingerprint": "sha256:" + hashlib.sha256(identity.session_id.encode("utf-8")).hexdigest(),
            "attested_at": self._clock().isoformat(),
        }
        self._registry.record_benchmark_review_identity_attestation(
            attestation, actor=actor, reason="Trusted identity provider attestation captured at review submission"
        )
        return result

    def adjudicate(
        self, request_context: Any, payload: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_adjudicator")
        if payload.get("adjudicator", {}).get("principal_id") != actor:
            raise RegistryIntegrityError("authenticated adjudicator must match the adjudication declaration")
        return self._registry.add_benchmark_adjudication(payload, actor=actor, reason=reason)

    def assign_review(
        self, request_context: Any, payload: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_curator")
        if payload.get("assigned_by") != actor:
            raise RegistryIntegrityError("authenticated curator must match the declared assigner")
        return self._registry.create_benchmark_review_assignment(
            payload, actor=actor, reason=reason
        )

    def transition_review_assignment(
        self, request_context: Any, assignment_id: str, *, event: str,
        at: str, reason: str, submitted_review_id: str | None = None
    ) -> dict[str, Any]:
        assignment = self._registry.get_benchmark_review_assignment(assignment_id)
        if assignment is None:
            raise RegistryIntegrityError(f"unknown benchmark review assignment: {assignment_id}")
        if event in {"accept", "submit"}:
            required_role = "benchmark_reviewer"
        elif event == "expire":
            required_role = "benchmark_curator"
        elif event == "cancel":
            required_role = ("benchmark_curator", "benchmark_reviewer")
        else:
            raise RegistryIntegrityError("unsupported assignment transition")
        actor = self._principal(request_context, required_role)
        if event in {"accept", "submit"} and actor != assignment["reviewer_id"]:
            raise RegistryIntegrityError("authenticated reviewer must match the assigned reviewer")
        if event == "expire" and actor != assignment["assigned_by"]:
            raise RegistryIntegrityError("authenticated curator must match the assigning curator")
        if event == "cancel" and actor not in {assignment["assigned_by"], assignment["reviewer_id"]}:
            raise RegistryIntegrityError("authenticated principal is not a participant in this assignment")
        return self._registry.transition_benchmark_review_assignment(
            assignment_id, event=event, actor=actor, at=at, reason=reason,
            submitted_review_id=submitted_review_id,
        )


    def provision_principal(
        self, request_context: Any, principal_id: str, *, display_name: str,
        roles: list[str], languages: list[str] | None = None,
        principal_kind: str = "unverified", reason: str
    ) -> dict[str, Any]:
        """Provision identities only through an authenticated security administrator."""
        actor = self._principal(request_context, "benchmark_security_admin")
        if actor == principal_id:
            raise RegistryIntegrityError("security administrators cannot provision themselves")
        return self._registry.register_benchmark_principal(
            principal_id, display_name=display_name, roles=roles,
            languages=languages, principal_kind=principal_kind,
            provisioned_by=actor, reason=reason,
        )

    def revoke_principal(
        self, request_context: Any, principal_id: str, *, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_security_admin")
        if actor == principal_id:
            raise RegistryIntegrityError("security administrators cannot revoke their own access")
        return self._registry.revoke_benchmark_principal(
            principal_id, actor=actor, reason=reason
        )

    def reinstate_principal(
        self, request_context: Any, principal_id: str, *, reason: str
    ) -> dict[str, Any]:
        actor = self._principal(request_context, "benchmark_security_admin")
        if actor == principal_id:
            raise RegistryIntegrityError("security administrators cannot reinstate their own access")
        return self._registry.reinstate_benchmark_principal(
            principal_id, actor=actor, reason=reason
        )

    def reviewer_assignment_queue(
        self, request_context: Any, *, status: str | None = None,
        language: str | None = None
    ) -> list[dict[str, Any]]:
        actor = self._principal(request_context, "benchmark_reviewer")
        principal = self._registry.get_benchmark_principal(actor)
        qualified = principal.get("languages", []) if principal else []
        if language is not None and language not in qualified:
            raise RegistryIntegrityError("reviewer is not qualified for the requested assignment language")
        return self._registry.list_benchmark_review_assignments(
            reviewer_id=actor, status=status, language=language,
        )
