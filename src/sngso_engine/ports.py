"""Ports that keep domain logic independent of storage technology."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from .models import (
    ArticleRecord,
    ClaimRecord,
    EditionRecord,
    EvidenceRecord,
    PermissionEvidenceRecord,
    PermissionGrantRecord,
    SourceEndpointRecord,
    SourceRecord,
    StoryRecord,
)
from .permissions import PermissionDecision


DomainRecordType = Literal["article", "story", "claim", "evidence", "edition"]
DomainRecord = ArticleRecord | StoryRecord | ClaimRecord | EvidenceRecord | EditionRecord


class SourcePermissionRepository(Protocol):
    def get_current_for_source(
        self, source_id: str, *, at: datetime
    ) -> dict[str, Any] | None:
        """Return the applicable permission record, or None if none applies."""
        ...


class SourceObservationRepository(Protocol):
    def append_observation(self, observation: dict[str, Any]) -> None:
        """Append an immutable observation; never silently overwrite one."""
        ...


class EditionRepository(Protocol):
    def get_latest_published(self) -> dict[str, Any] | None:
        """Return the last known-good published edition, if one exists."""
        ...

    def publish_immutable(self, edition: dict[str, Any]) -> None:
        """Persist a new immutable edition snapshot."""
        ...


class SourceGovernanceRepository(Protocol):
    """Port for publisher identity, endpoint, evidence, and grant storage."""

    def add_source(self, payload: dict[str, Any]) -> SourceRecord: ...
    def add_endpoint(self, payload: dict[str, Any]) -> SourceEndpointRecord: ...
    def add_permission_evidence(self, payload: dict[str, Any]) -> PermissionEvidenceRecord: ...
    def add_permission_grant(self, payload: dict[str, Any]) -> PermissionGrantRecord: ...
    def get_source(self, source_id: str) -> SourceRecord | None: ...
    def get_endpoint(self, endpoint_id: str) -> SourceEndpointRecord | None: ...
    def get_permission_evidence(self, evidence_id: str) -> PermissionEvidenceRecord | None: ...
    def get_permission_grant(self, grant_id: str) -> PermissionGrantRecord | None: ...
    def evaluate_grant(
        self,
        grant_id: str,
        operation: str,
        *,
        endpoint_id: str,
        now: datetime | None = None,
    ) -> PermissionDecision: ...
    def transition_permission_grant(
        self,
        grant_id: str,
        new_status: str,
        *,
        actor: str,
        reason: str,
        at: str | None = None,
    ) -> PermissionGrantRecord: ...


class EditorialRepository(Protocol):
    """Port for versioned article, story, claim, evidence, and edition records."""

    def add_domain_record(
        self, record_type: DomainRecordType, payload: dict[str, Any]
    ) -> DomainRecord: ...
    def get_domain_record(
        self, record_type: DomainRecordType, record_id: str
    ) -> DomainRecord | None: ...
    def revise_domain_record(
        self,
        record_type: DomainRecordType,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> DomainRecord: ...
    def story_reporting_independence(self, story_id: str) -> dict[str, Any]: ...


class BenchmarkReviewRepository(Protocol):
    """Versioned human review records with append-only decision history."""

    def add_benchmark_adjudication(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_adjudication(self, adjudication_id: str) -> dict[str, Any] | None: ...
    def register_benchmark_principal(
        self, principal_id: str, *, display_name: str, roles: list[str],
        provisioned_by: str, reason: str, languages: list[str] | None = None
    ) -> dict[str, Any]: ...
    def get_benchmark_principal(self, principal_id: str) -> dict[str, Any] | None: ...
    def revoke_benchmark_principal(self, principal_id: str, *, actor: str, reason: str) -> dict[str, Any]: ...
    def register_benchmark_artifact(
        self, artifact_id: str, *, language: str, content: Any,
        actor: str, reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_artifact(self, artifact_id: str) -> dict[str, Any] | None: ...
    def create_benchmark_review_assignment(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_review_assignment(self, assignment_id: str) -> dict[str, Any] | None: ...
    def benchmark_review_assignment_history(self, assignment_id: str) -> list[dict[str, Any]]: ...
    def list_benchmark_review_assignments(
        self, *, reviewer_id: str | None = None, status: str | None = None,
        language: str | None = None
    ) -> list[dict[str, Any]]: ...
    def transition_benchmark_review_assignment(
        self, assignment_id: str, *, event: str, actor: str, at: str,
        reason: str, submitted_review_id: str | None = None
    ) -> dict[str, Any]: ...
    def register_benchmark_study_execution(
        self, payload: dict[str, Any], *, actor: str, identity: dict[str, Any], reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_study_execution(self, execution_id: str) -> dict[str, Any] | None: ...
    def list_benchmark_study_executions(self, study_id: str) -> list[dict[str, Any]]: ...
    def register_benchmark_acceptance_review(
        self, payload: dict[str, Any], *, actor: str, identity: dict[str, Any], reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_acceptance_review(self, review_id: str) -> dict[str, Any] | None: ...
    def list_benchmark_acceptance_reviews(
        self, *, study_id: str | None = None, execution_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    def register_benchmark_study_protocol(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_study_protocol(
        self, study_id: str, study_version: int | None = None
    ) -> dict[str, Any] | None: ...
    def benchmark_study_protocol_history(self, study_id: str) -> list[dict[str, Any]]: ...
    def list_benchmark_study_protocols(
        self, *, benchmark_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    def add_benchmark_score(
        self, score: dict[str, Any], *, assignment_id: str, actor: str,
        identity: dict[str, Any], reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_score(self, score_id: str) -> dict[str, Any] | None: ...
    def list_benchmark_scores(
        self, *, artifact_id: str | None = None, item_id: str | None = None,
        evaluator_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    def add_benchmark_review(
        self, review: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]: ...
    def revise_benchmark_review(
        self, review_id: str, review: dict[str, Any], *,
        expected_revision: int, actor: str, reason: str
    ) -> dict[str, Any]: ...
    def get_benchmark_review(self, review_id: str) -> dict[str, Any] | None: ...
    def benchmark_review_history(self, review_id: str) -> list[dict[str, Any]]: ...
    def benchmark_review_quality_report(self, review_id: str) -> dict[str, Any]: ...
    def record_benchmark_review_identity_attestation(
        self, attestation: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]: ...


class AuditRepository(Protocol):
    """Port for append-only decision and revision history."""

    def audit_events(self, *, entity_id: str | None = None) -> list[dict[str, Any]]: ...
    def verify_audit_chain(self) -> bool: ...


class CollectionObservationRepository(Protocol):
    """Append-only sink for permission-checked collection observations."""

    def append_many(self, observations: tuple[Any, ...]) -> None: ...
    def collection_observations(
        self, *, source_id: str | None = None
    ) -> list[dict[str, Any]]: ...


class IngestionRepository(Protocol):
    """Port for turning one stored collection observation into an article identity."""

    def get_collection_observation(self, observation_id: str) -> dict[str, Any] | None: ...
    def get_endpoint(self, endpoint_id: str) -> SourceEndpointRecord | None: ...
    def ingest_article_observation(
        self,
        observation_id: str,
        article_payload: dict[str, Any],
        *,
        duplicate_fingerprint: str | None,
    ) -> dict[str, Any]: ...


class EngineRepository(
    SourceGovernanceRepository,
    EditorialRepository,
    AuditRepository,
    CollectionObservationRepository,
    IngestionRepository,
    BenchmarkReviewRepository,
    Protocol,
):
    """Combined repository port implemented by the SQLite adapter."""
