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


    def review_learning_object(
        self, request_context: Any, learning_object_id: str, *,
        expected_revision: int, decision: str, dimensions: dict[str, str],
        note: str, reason: str, mcq_dimensions: dict[str, str] | None = None,
    ) -> Any:
        """Record a language-specific editorial decision with provider-backed reviewer provenance."""
        identity = self._identity(request_context)
        actor = identity.principal_id
        principal = self._registry.get_benchmark_principal(actor)
        if (
            principal is None or not principal["active"]
            or "benchmark_reviewer" not in principal["roles"]
            or principal.get("principal_kind") != "human"
        ):
            raise RegistryIntegrityError("editorial review requires an active human-classified benchmark reviewer")
        now = self._clock()
        if now - identity.authenticated_at > timedelta(minutes=15):
            raise RegistryIntegrityError("editorial review requires recent authentication within 15 minutes")
        if (
            not identity.subject.strip() or not identity.authentication_methods
            or not identity.authentication_context.strip()
            or not identity.provider_key_id.strip() or not identity.provider_adapter.strip()
        ):
            raise RegistryIntegrityError("editorial reviewer requires provider-backed identity and authentication context")
        current = self._registry.get_learning_object(learning_object_id)
        if current is None:
            raise RegistryIntegrityError(f"unknown learning object: {learning_object_id}")
        import json
        payload = json.loads(current.payload_json)
        if current.revision != expected_revision:
            raise RegistryIntegrityError("learning object revision conflict")
        language = payload.get("language")
        if language not in {"bn", "en"} or language not in principal.get("languages", []):
            raise RegistryIntegrityError("authenticated reviewer is not qualified for this editorial language")
        if decision not in {"approved", "rejected"}:
            raise RegistryIntegrityError("editorial review decision must be approved or rejected")
        is_mcq = payload.get("kind") == "mcq_question"
        if is_mcq:
            required_mcq_dimensions = {
                "single_defensible_key", "stem_clarity_and_precision",
                "distractor_plausibility", "distractors_unambiguously_incorrect",
                "distractor_rationale_quality", "learner_level_fit",
                "misconception_alignment", "option_parallelism",
                "no_accidental_clues", "explanation_fidelity",
            }
            if not isinstance(mcq_dimensions, dict) or set(mcq_dimensions) != required_mcq_dimensions:
                raise RegistryIntegrityError("MCQ review requires every pedagogical rubric dimension")
            if any(value not in {"pass", "needs_revision"} for value in mcq_dimensions.values()):
                raise RegistryIntegrityError("MCQ review rubric values must be pass or needs_revision")
            has_findings = any(value == "needs_revision" for value in mcq_dimensions.values())
            if decision == "approved" and has_findings:
                raise RegistryIntegrityError("approved MCQ review cannot contain needs_revision findings")
            if decision == "rejected" and not has_findings:
                raise RegistryIntegrityError("rejected MCQ review must identify a needs_revision finding")
        elif mcq_dimensions is not None:
            raise RegistryIntegrityError("MCQ pedagogical dimensions are only valid for MCQ learning objects")
        reviewed_at = now.isoformat()
        rubric_version = "bn-editorial-v1" if language == "bn" else "en-editorial-v1"
        payload.update({
            "status": decision,
            "reviewed_at": reviewed_at,
            "reviewed_by": actor,
            "review_note": note,
            "language_review": {
                "language": language,
                "rubric_version": rubric_version,
                "reviewer_id": actor,
                "reviewed_at": reviewed_at,
                "note": note,
                "dimensions": dimensions,
                "identity_attestation": {
                    "principal_id": actor,
                    "issuer": identity.issuer,
                    "subject": identity.subject,
                    "provider_key_id": identity.provider_key_id,
                    "provider_adapter": identity.provider_adapter,
                    "authenticated_at": identity.authenticated_at.isoformat(),
                    "attested_at": reviewed_at,
                    "authentication_methods": list(identity.authentication_methods),
                    "authentication_context": identity.authentication_context,
                },
            },
        })
        if is_mcq:
            payload["mcq_review"] = {
                "language": language,
                "rubric_version": "mcq-editorial-v1",
                "reviewer_id": actor,
                "reviewed_at": reviewed_at,
                "note": note,
                "dimensions": mcq_dimensions,
                "identity_attestation": {
                    "principal_id": actor,
                    "issuer": identity.issuer,
                    "subject": identity.subject,
                    "provider_key_id": identity.provider_key_id,
                    "provider_adapter": identity.provider_adapter,
                    "authenticated_at": identity.authenticated_at.isoformat(),
                    "attested_at": reviewed_at,
                    "authentication_methods": list(identity.authentication_methods),
                    "authentication_context": identity.authentication_context,
                },
            }
        return self._registry.revise_learning_object(
            learning_object_id, payload, expected_revision=expected_revision,
            actor=actor, reason=reason,
        )

    def submit_score(
        self, request_context: Any, assignment_id: str,
        score: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        """Record a score only for the authenticated, recently verified assignee."""
        identity = self._identity(request_context)
        actor = identity.principal_id
        principal = self._registry.get_benchmark_principal(actor)
        if (
            principal is None or not principal["active"]
            or "benchmark_reviewer" not in principal["roles"]
            or principal.get("principal_kind") != "human"
        ):
            raise RegistryIntegrityError("scoring requires an active human-classified benchmark reviewer")
        if self._clock() - identity.authenticated_at > timedelta(minutes=15):
            raise RegistryIntegrityError("score submission requires recent authentication within 15 minutes")
        if not identity.subject.strip() or not identity.authentication_methods or not identity.authentication_context.strip():
            raise RegistryIntegrityError("identity provider must supply subject and authentication context for scoring")
        if not identity.provider_key_id.strip() or not identity.provider_adapter.strip():
            raise RegistryIntegrityError("identity provider must identify its verification key and adapter")
        if score.get("evaluator_id") != actor:
            raise RegistryIntegrityError("score evaluator must match the authenticated principal")
        return self._registry.add_benchmark_score(
            score, assignment_id=assignment_id, actor=actor,
            identity={
                "issuer": identity.issuer, "subject": identity.subject,
                "provider_key_id": identity.provider_key_id,
                "provider_adapter": identity.provider_adapter,
                "authenticated_at": identity.authenticated_at.isoformat(),
            },
            reason=reason,
        )



    def submit_acceptance_review(
        self, request_context: Any, payload: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        """Submit an immutable independent acceptance decision from its authenticated reviewer."""
        identity = self._identity(request_context)
        actor = identity.principal_id
        principal = self._registry.get_benchmark_principal(actor)
        if (
            principal is None or not principal["active"]
            or "benchmark_acceptance_reviewer" not in principal["roles"]
            or principal.get("principal_kind") != "human"
        ):
            raise RegistryIntegrityError("acceptance review requires an active human-classified acceptance reviewer")
        if self._clock() - identity.authenticated_at > timedelta(minutes=15):
            raise RegistryIntegrityError("acceptance review requires recent authentication within 15 minutes")
        if (
            not identity.subject.strip() or not identity.authentication_methods
            or not identity.authentication_context.strip()
            or not identity.provider_key_id.strip() or not identity.provider_adapter.strip()
        ):
            raise RegistryIntegrityError("acceptance reviewer requires provider-backed identity and authentication context")
        if payload.get("reviewer_id") != actor:
            raise RegistryIntegrityError("acceptance reviewer must match the authenticated principal")
        return self._registry.register_benchmark_acceptance_review(
            payload, actor=actor,
            identity={
                "issuer": identity.issuer, "subject": identity.subject,
                "provider_key_id": identity.provider_key_id,
                "provider_adapter": identity.provider_adapter,
                "authenticated_at": identity.authenticated_at.isoformat(),
            },
            reason=reason,
        )

    def get_acceptance_review(
        self, request_context: Any, review_id: str
    ) -> dict[str, Any] | None:
        actor = self._principal(
            request_context,
            ("benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin",
             "benchmark_methodologist", "benchmark_acceptance_reviewer"),
        )
        record = self._registry.get_benchmark_acceptance_review(review_id)
        if record is not None and record["reviewer_id"] != actor:
            principal = self._registry.get_benchmark_principal(actor)
            if not any(role in principal["roles"] for role in (
                "benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin",
                "benchmark_methodologist",
            )):
                raise RegistryIntegrityError("acceptance reviewers may only retrieve their own submitted decisions")
        return record

    def list_acceptance_reviews(
        self, request_context: Any, *, study_id: str | None = None,
        execution_id: str | None = None
    ) -> list[dict[str, Any]]:
        self._principal(
            request_context,
            ("benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin",
             "benchmark_methodologist"),
        )
        return self._registry.list_benchmark_acceptance_reviews(
            study_id=study_id, execution_id=execution_id,
        )

    def register_study_execution(
        self, request_context: Any, payload: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        """Register completed study evidence using an authenticated methodologist."""
        identity = self._identity(request_context)
        actor = identity.principal_id
        principal = self._registry.get_benchmark_principal(actor)
        if (
            principal is None or not principal["active"]
            or "benchmark_methodologist" not in principal["roles"]
            or principal.get("principal_kind") != "human"
        ):
            raise RegistryIntegrityError("study execution requires an active human-classified methodologist")
        now = self._clock()
        if now - identity.authenticated_at > timedelta(minutes=15):
            raise RegistryIntegrityError("study execution submission requires recent authentication within 15 minutes")
        if not identity.subject.strip() or not identity.authentication_methods or not identity.authentication_context.strip():
            raise RegistryIntegrityError("methodologist identity provider must supply subject and authentication context")
        if not identity.provider_key_id.strip() or not identity.provider_adapter.strip():
            raise RegistryIntegrityError("methodologist identity provider must identify its verification key and adapter")
        if payload.get("analysis", {}).get("methodologist_id") != actor:
            raise RegistryIntegrityError("analysis methodologist must match the authenticated principal")
        return self._registry.register_benchmark_study_execution(
            payload, actor=actor,
            identity={
                "issuer": identity.issuer, "subject": identity.subject,
                "provider_key_id": identity.provider_key_id,
                "provider_adapter": identity.provider_adapter,
                "authenticated_at": identity.authenticated_at.isoformat(),
            },
            reason=reason,
        )

    def get_study_execution(
        self, request_context: Any, execution_id: str
    ) -> dict[str, Any] | None:
        self._principal(request_context, ("benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin", "benchmark_methodologist"))
        return self._registry.get_benchmark_study_execution(execution_id)

    def list_study_executions(
        self, request_context: Any, study_id: str
    ) -> list[dict[str, Any]]:
        self._principal(request_context, ("benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin", "benchmark_methodologist"))
        return self._registry.list_benchmark_study_executions(study_id)

    def register_study_protocol(
        self, request_context: Any, payload: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        """Register a study protocol as an authenticated benchmark curator."""
        actor = self._principal(request_context, "benchmark_curator")
        return self._registry.register_benchmark_study_protocol(
            payload, actor=actor, reason=reason
        )

    def get_study_protocol(
        self, request_context: Any, study_id: str, study_version: int | None = None
    ) -> dict[str, Any] | None:
        self._principal(request_context, ("benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin"))
        return self._registry.get_benchmark_study_protocol(study_id, study_version)

    def study_protocol_history(
        self, request_context: Any, study_id: str
    ) -> list[dict[str, Any]]:
        self._principal(request_context, ("benchmark_curator", "benchmark_adjudicator", "benchmark_security_admin"))
        return self._registry.benchmark_study_protocol_history(study_id)

    def evaluator_agreement_report(
        self, request_context: Any, artifact_id: str, item_id: str
    ) -> dict[str, Any]:
        """Expose peer-score comparisons to curators/adjudicators, not reviewers."""
        self._principal(request_context, ("benchmark_curator", "benchmark_adjudicator"))
        return self._registry.benchmark_evaluator_agreement_report(artifact_id, item_id)

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
