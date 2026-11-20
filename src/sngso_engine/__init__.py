"""Sarangsho Plus greenfield engine package."""

__version__ = "0.1.0"

from .benchmark_reviews import validate_human_benchmark_review
from .benchmark_adjudication import validate_benchmark_adjudication
from .benchmarks import evaluate_editorial_benchmark
from .canonical import canonical_json_bytes, sha256_fingerprint
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
from .ports import (
    AuditRepository,
    CollectionObservationRepository,
    EditorialRepository,
    EngineRepository,
    IngestionRepository,
    SourceGovernanceRepository,
)
from .registry import RegistryIntegrityError, RegistryMigrationError, SQLiteRegistry

__all__ = [
    "ArticleRecord",
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
    "EditorialRepository",
    "EngineRepository",
    "EvidenceRecord",
    "LearningObjectRecord",
    "GlossaryEntryRecord",
    "InMemoryDomainRateLimiter",
    "InMemoryObservationSink",
    "IngestionError",
    "IngestionRepository",
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
    "validate_human_benchmark_review",\n    "validate_benchmark_adjudication",
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
