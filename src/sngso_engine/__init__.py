"""Sarangsho Plus greenfield engine package."""

from .edition_assembly import AssembledBilingualEdition, EditionAssemblyError, assemble_bilingual_edition_artifact
from .edition_export import StagingExportError, VerifiedStagingExport, build_verified_staging_export
from .edition_staging import (
    EditionStagingError, get_edition_staging_state, list_edition_staging_events,
    promote_edition_to_staging, rollback_edition_staging,
)

__version__ = "0.1.0"

from .benchmark_reviews import validate_human_benchmark_review
from .benchmark_assignments import (
    BenchmarkAssignmentError,
    transition_benchmark_assignment,
    validate_benchmark_assignment,
)
from .benchmark_adjudication import validate_benchmark_adjudication
from .benchmark_scoring import validate_benchmark_scoring
from .benchmark_agreement import BenchmarkAgreementError, benchmark_evaluator_agreement
from .benchmark_study import BenchmarkStudyError, validate_benchmark_study_protocol
from .benchmark_study_execution import BenchmarkStudyExecutionError, validate_benchmark_study_execution
from .benchmark_acceptance_review import BenchmarkAcceptanceReviewError, validate_benchmark_acceptance_review
from .benchmarks import evaluate_editorial_benchmark
from .canonical import canonical_json_bytes, sha256_fingerprint
from .edition_artifacts import (
    EditionArtifactIntegrityError,
    VerifiedEditionArtifact,
    VerifiedPipelineRun,
    VerifiedRegistryEdition,
    verify_registry_backed_edition_artifact,
    verify_edition_artifact,
    verify_pipeline_run_for_artifact,
)
from .collection import (
    CollectionError,
    CollectionExecutionContext,
    CollectionObservation,
    CollectionRequest,
    CollectionResult,
    ConnectorCapabilities,
    InMemoryDomainRateLimiter,
    InMemoryObservationSink,
    PermissionFirstCollector,
)
from .contracts import ContractValidationError, load_schema, validate_payload
from .language_variants import BilingualVariantError, verify_independent_bilingual_variants
from .ingestion import (
    IngestionError,
    canonicalize_article_url,
    ingest_observation,
    normalize_language_tag,
    normalize_observation,
    normalize_text,
    normalize_timestamp,
)
from .models import (
    ArticleRecord,
    ClaimRecord,
    ClaimRelationRecord,
    EditionRecord,
    EvidenceRecord,
    LearningObjectRecord,
    GlossaryEntryRecord,
    PermissionEvidenceRecord,
    PermissionGrantRecord,
    PerspectiveRecord,
    SourceEndpointRecord,
    SourceRecord,
    StoryDecisionRecord,
    StoryRecord,
)
from .permissions import PermissionDecision, evaluate_permission
from .operations import OperationalReportError, build_operational_health_report
from .ports import (
    AuditRepository,
    CollectionObservationRepository,
    EditorialRepository,
    EngineRepository,
    IngestionRepository,
    PipelineRunRepository,
    SourceGovernanceRepository,
)
from .registry import RegistryIntegrityError, RegistryMigrationError, SQLiteRegistry

__all__ = [
    "ArticleRecord",
    "BenchmarkAssignmentError",
    "BilingualVariantError",
    "BenchmarkStudyError",
    "BenchmarkStudyExecutionError",
    "BenchmarkAcceptanceReviewError",
    "AuditRepository",
    "ClaimRecord",
    "ClaimRelationRecord",
    "CollectionError",
    "CollectionExecutionContext",
    "CollectionObservation",
    "CollectionObservationRepository",
    "CollectionRequest",
    "CollectionResult",
    "ConnectorCapabilities",
    "ContractValidationError",
    "EditionRecord",
    "AssembledBilingualEdition",
    "EditionAssemblyError",
    "assemble_bilingual_edition_artifact",
    "StagingExportError",
    "VerifiedStagingExport",
    "build_verified_staging_export",
    "EditionStagingError",
    "get_edition_staging_state",
    "list_edition_staging_events",
    "promote_edition_to_staging",
    "rollback_edition_staging",
    "EditionArtifactIntegrityError",
    "VerifiedEditionArtifact",
    "VerifiedPipelineRun",
    "VerifiedRegistryEdition",
    "verify_registry_backed_edition_artifact",
    "verify_edition_artifact",
    "verify_pipeline_run_for_artifact",
    "EditorialRepository",
    "EngineRepository",
    "EvidenceRecord",
    "LearningObjectRecord",
    "GlossaryEntryRecord",
    "InMemoryDomainRateLimiter",
    "InMemoryObservationSink",
    "IngestionError",
    "IngestionRepository",
    "PipelineRunRepository",
    "OperationalReportError",
    "build_operational_health_report",
    "PermissionDecision",
    "PermissionFirstCollector",
    "PermissionEvidenceRecord",
    "PermissionGrantRecord",
    "PerspectiveRecord",
    "RegistryIntegrityError",
    "RegistryMigrationError",
    "SQLiteRegistry",
    "SourceEndpointRecord",
    "SourceGovernanceRepository",
    "SourceRecord",
    "StoryDecisionRecord",
    "StoryRecord",
    "canonical_json_bytes",
    "canonicalize_article_url",
    "evaluate_editorial_benchmark",
    "validate_human_benchmark_review",
    "verify_independent_bilingual_variants",
    "validate_benchmark_assignment",
    "transition_benchmark_assignment",
    "validate_benchmark_adjudication",
    "validate_benchmark_scoring",
    "BenchmarkAgreementError",
    "benchmark_evaluator_agreement",
    "validate_benchmark_study_protocol",
    "validate_benchmark_study_execution",
    "validate_benchmark_acceptance_review",
    "ingest_observation",
    "normalize_language_tag",
    "normalize_observation",
    "normalize_text",
    "normalize_timestamp",
    "evaluate_permission",
    "load_schema",
    "sha256_fingerprint",
    "validate_payload",
]
