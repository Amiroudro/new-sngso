"""Sarangsho Plus greenfield engine package."""

__version__ = "0.1.0"

from .canonical import canonical_json_bytes, sha256_fingerprint
from .collection import (
    CollectionError,
    CollectionObservation,
    CollectionRequest,
    CollectionResult,
    ConnectorCapabilities,
    InMemoryObservationSink,
    PermissionFirstCollector,
)
from .contracts import ContractValidationError, load_schema, validate_payload
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
from .permissions import PermissionDecision, evaluate_permission
from .ports import AuditRepository, EditorialRepository, EngineRepository, SourceGovernanceRepository
from .registry import RegistryIntegrityError, RegistryMigrationError, SQLiteRegistry

__all__ = [
    "ArticleRecord",
    "AuditRepository",
    "ClaimRecord",
    "CollectionError",
    "CollectionObservation",
    "CollectionRequest",
    "CollectionResult",
    "ConnectorCapabilities",
    "ContractValidationError",
    "EditionRecord",
    "EditorialRepository",
    "EngineRepository",
    "EvidenceRecord",
    "InMemoryObservationSink",
    "PermissionDecision",
    "PermissionFirstCollector",
    "PermissionEvidenceRecord",
    "PermissionGrantRecord",
    "RegistryIntegrityError",
    "RegistryMigrationError",
    "SQLiteRegistry",
    "SourceEndpointRecord",
    "SourceGovernanceRepository",
    "SourceRecord",
    "StoryRecord",
    "canonical_json_bytes",
    "evaluate_permission",
    "load_schema",
    "sha256_fingerprint",
    "validate_payload",
]
