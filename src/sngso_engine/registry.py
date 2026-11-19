"""SQLite-backed source registry with cross-record checks and an audit chain."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from .canonical import canonical_json_bytes, sha256_fingerprint
from .contracts import validate_payload
from .domains import host_in_domain_allowlist, valid_domain_allowlist, valid_http_uri
from .models import (
    ArticleRecord,
    ClaimRecord,
    EditionRecord,
    EvidenceRecord,
    PermissionEvidenceRecord,
    PermissionGrantRecord,
    SourceEndpointRecord,
    SourceRecord,
    StoryDecisionRecord,
    StoryRecord,
    TimelineEventRecord,
    PerspectiveRecord,
    ClaimRelationRecord,
)
from .permissions import OPERATIONS, PermissionDecision, evaluate_permission as evaluate_permission_record


POLICY_VERSION = "permission-evaluator/1.0.0"
_SCHEMA_VERSION = 9


class RegistryIntegrityError(ValueError):
    """Raised when a record references missing or incompatible registry data."""


class RegistryMigrationError(RuntimeError):
    """Raised when the database schema cannot be migrated safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _aware_datetime(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise RegistryIntegrityError(f"{field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryIntegrityError(f"{field} must be a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegistryIntegrityError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)



def _host_in_domains(host: str, domains: Any) -> bool:
    return host_in_domain_allowlist(host, domains)


def _json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


_MIGRATION_1 = (
    """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )""",
    """CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        publisher_name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
    )""",
    """CREATE TABLE source_endpoints (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        endpoint_uri TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (source_id, id),
        UNIQUE (id, source_id),
        UNIQUE (source_id, endpoint_uri),
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE permission_evidence (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        review_status TEXT NOT NULL,
        content_fingerprint TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (source_id, id),
        UNIQUE (id, source_id),
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE permission_grants (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        status TEXT NOT NULL,
        effective_from TEXT NOT NULL,
        effective_until TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (source_id, id),
        UNIQUE (id, source_id),
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE grant_endpoints (
        grant_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        PRIMARY KEY (grant_id, endpoint_id),
        FOREIGN KEY (grant_id, source_id)
            REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, source_id)
            REFERENCES source_endpoints(id, source_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE grant_evidence (
        grant_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        PRIMARY KEY (grant_id, evidence_id),
        UNIQUE (grant_id, source_id, evidence_id),
        FOREIGN KEY (grant_id, source_id)
            REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT,
        FOREIGN KEY (evidence_id, source_id)
            REFERENCES permission_evidence(id, source_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE grant_operation_evidence (
        grant_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        PRIMARY KEY (grant_id, operation, evidence_id),
        FOREIGN KEY (grant_id, source_id, evidence_id)
            REFERENCES grant_evidence(grant_id, source_id, evidence_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE permission_grant_revisions (
        grant_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (grant_id, revision),
        FOREIGN KEY (grant_id) REFERENCES permission_grants(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE domain_records (
        record_type TEXT NOT NULL CHECK (record_type IN ('article', 'story', 'claim', 'evidence', 'edition')),
        id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (record_type, id)
    )""",
    """CREATE TABLE domain_record_revisions (
        record_type TEXT NOT NULL,
        record_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (record_type, record_id, revision),
        FOREIGN KEY (record_type, record_id)
            REFERENCES domain_records(record_type, id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE audit_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        policy_version TEXT,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        previous_hash TEXT,
        event_hash TEXT NOT NULL UNIQUE
    )""",
    "CREATE INDEX idx_endpoints_source ON source_endpoints(source_id)",
    "CREATE INDEX idx_evidence_source ON permission_evidence(source_id, review_status)",
    "CREATE INDEX idx_grants_source_status ON permission_grants(source_id, status)",
    "CREATE INDEX idx_audit_entity ON audit_events(entity_type, entity_id, sequence)",
    """CREATE TRIGGER audit_events_no_update
        BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END""",
    """CREATE TRIGGER audit_events_no_delete
        BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END""",
    """CREATE TRIGGER grant_revisions_no_update
        BEFORE UPDATE ON permission_grant_revisions
        BEGIN SELECT RAISE(ABORT, 'grant revisions are append-only'); END""",
    """CREATE TRIGGER grant_revisions_no_delete
        BEFORE DELETE ON permission_grant_revisions
        BEGIN SELECT RAISE(ABORT, 'grant revisions are append-only'); END""",
    """CREATE TRIGGER domain_revisions_no_update
        BEFORE UPDATE ON domain_record_revisions
        BEGIN SELECT RAISE(ABORT, 'domain record revisions are append-only'); END""",
    """CREATE TRIGGER domain_revisions_no_delete
        BEFORE DELETE ON domain_record_revisions
        BEGIN SELECT RAISE(ABORT, 'domain record revisions are append-only'); END""",
)




_MIGRATION_2 = (
    """CREATE TABLE collection_observations (
        observation_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK (grant_revision >= 1),
        operation TEXT NOT NULL CHECK (operation IN ('discovery', 'metadata')),
        connector_name TEXT NOT NULL,
        connector_version TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        locator TEXT NOT NULL,
        final_locator TEXT,
        content_fingerprint TEXT,
        response_status INTEGER CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
        content_type TEXT,
        metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
        permission_reason TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, source_id)
            REFERENCES source_endpoints(id, source_id) ON DELETE RESTRICT,
        FOREIGN KEY (grant_id, source_id)
            REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_collection_observations_source_time ON collection_observations(source_id, observed_at)",
    "CREATE INDEX idx_collection_observations_endpoint_time ON collection_observations(endpoint_id, observed_at)",
    """CREATE TRIGGER collection_observations_no_update
        BEFORE UPDATE ON collection_observations
        BEGIN SELECT RAISE(ABORT, 'collection observations are append-only'); END""",
    """CREATE TRIGGER collection_observations_no_delete
        BEFORE DELETE ON collection_observations
        BEGIN SELECT RAISE(ABORT, 'collection observations are append-only'); END""",
)


_MIGRATION_3 = (
    """ALTER TABLE collection_observations
       ADD COLUMN http_method TEXT NOT NULL DEFAULT 'GET'
       CHECK (http_method = 'GET')""",
)


_MIGRATION_4 = (
    """CREATE TABLE ingestion_decisions (
        decision_id TEXT PRIMARY KEY,
        observation_id TEXT NOT NULL,
        decision_revision INTEGER NOT NULL CHECK (decision_revision >= 1),
        source_id TEXT NOT NULL,
        article_id TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN (
            'created', 'reused', 'possible_duplicate', 'confirmed_duplicate', 'distinct'
        )),
        duplicate_of_article_id TEXT,
        fingerprint TEXT CHECK (fingerprint IS NULL OR (length(fingerprint) = 71 AND substr(fingerprint, 1, 7) = 'sha256:' AND substr(fingerprint, 8) NOT GLOB '*[^0-9a-f]*')),
        canonical_uri TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
        decided_at TEXT NOT NULL,
        UNIQUE (observation_id, decision_revision),
        UNIQUE (observation_id, decision_id),
        FOREIGN KEY (observation_id) REFERENCES collection_observations(observation_id) ON DELETE RESTRICT,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE ingestion_current (
        observation_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        FOREIGN KEY (observation_id) REFERENCES collection_observations(observation_id) ON DELETE RESTRICT,
        FOREIGN KEY (observation_id, decision_id)
            REFERENCES ingestion_decisions(observation_id, decision_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_ingestion_fingerprint ON ingestion_decisions(fingerprint, source_id, decided_at)",
    "CREATE INDEX idx_article_source_uri ON domain_records(json_extract(payload_json, '$.source_id'), json_extract(payload_json, '$.canonical_uri')) WHERE record_type = 'article'",
    """CREATE TRIGGER ingestion_decisions_validate_article
        BEFORE INSERT ON ingestion_decisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'article' AND id = NEW.article_id
                  AND json_extract(payload_json, '$.source_id') = NEW.source_id
                  AND json_extract(payload_json, '$.canonical_uri') = NEW.canonical_uri
            ) THEN RAISE(ABORT, 'ingestion decision article/source/URI mismatch') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM collection_observations
                WHERE observation_id = NEW.observation_id AND source_id = NEW.source_id
            ) THEN RAISE(ABORT, 'ingestion decision observation/source mismatch') END;
            SELECT CASE WHEN NEW.duplicate_of_article_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'article' AND id = NEW.duplicate_of_article_id
                  AND json_extract(payload_json, '$.source_id') != NEW.source_id
            ) THEN RAISE(ABORT, 'ingestion duplicate target must be a different source article') END;
        END""",
    """CREATE TRIGGER ingestion_decisions_no_update
        BEFORE UPDATE ON ingestion_decisions
        BEGIN SELECT RAISE(ABORT, 'ingestion decisions are append-only'); END""",
    """CREATE TRIGGER ingestion_decisions_no_delete
        BEFORE DELETE ON ingestion_decisions
        BEGIN SELECT RAISE(ABORT, 'ingestion decisions are append-only'); END""",
    """CREATE TRIGGER ingestion_current_validate
        BEFORE INSERT ON ingestion_current
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM ingestion_decisions
                WHERE observation_id = NEW.observation_id AND decision_id = NEW.decision_id
            ) THEN RAISE(ABORT, 'current ingestion decision does not match observation') END;
        END""",
    """CREATE TRIGGER ingestion_current_validate_update
        BEFORE UPDATE ON ingestion_current
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM ingestion_decisions
                WHERE observation_id = NEW.observation_id AND decision_id = NEW.decision_id
            ) THEN RAISE(ABORT, 'current ingestion decision does not match observation') END;
        END""",
    """CREATE TRIGGER ingestion_current_no_delete
        BEFORE DELETE ON ingestion_current
        BEGIN SELECT RAISE(ABORT, 'current ingestion state cannot be deleted'); END""",
)


_MIGRATION_5 = (
    """CREATE TABLE story_decisions (
        id TEXT PRIMARY KEY,
        operation TEXT NOT NULL CHECK (operation IN ('merge', 'split')),
        canonical_story_id TEXT NOT NULL,
        story_ids_json TEXT NOT NULL CHECK (json_valid(story_ids_json)),
        article_ids_json TEXT NOT NULL CHECK (json_valid(article_ids_json)),
        decided_by TEXT NOT NULL,
        rationale TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
    )""",
    "CREATE INDEX idx_story_decisions_decided_at ON story_decisions(decided_at, id)",
    """CREATE TRIGGER story_decisions_no_update
        BEFORE UPDATE ON story_decisions
        BEGIN SELECT RAISE(ABORT, 'story decisions are append-only'); END""",
    """CREATE TRIGGER story_decisions_no_delete
        BEFORE DELETE ON story_decisions
        BEGIN SELECT RAISE(ABORT, 'story decisions are append-only'); END""",
)


_MIGRATION_6 = (
    """CREATE TABLE story_decision_applications (
        application_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) BETWEEN 1 AND 200),
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 4000),
        decision_fingerprint TEXT NOT NULL CHECK (
            length(decision_fingerprint) = 71
            AND substr(decision_fingerprint, 1, 7) = 'sha256:'
            AND substr(decision_fingerprint, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        impact_fingerprint TEXT NOT NULL CHECK (
            length(impact_fingerprint) = 71
            AND substr(impact_fingerprint, 1, 7) = 'sha256:'
            AND substr(impact_fingerprint, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        result_json TEXT NOT NULL CHECK (json_valid(result_json)),
        FOREIGN KEY (decision_id) REFERENCES story_decisions(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_story_decision_applications_applied_at ON story_decision_applications(applied_at, application_id)",
    """CREATE TRIGGER story_decision_applications_no_update
        BEFORE UPDATE ON story_decision_applications
        BEGIN SELECT RAISE(ABORT, 'story decision applications are append-only'); END""",
    """CREATE TRIGGER story_decision_applications_no_delete
        BEFORE DELETE ON story_decision_applications
        BEGIN SELECT RAISE(ABORT, 'story decision applications are append-only'); END""",
)


_MIGRATION_7 = (
    """CREATE TABLE timeline_events (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        occurred_sort_key TEXT,
        precision TEXT NOT NULL CHECK (precision IN (
            'instant', 'date', 'range', 'approximate', 'unknown'
        )),
        supersedes_event_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id),
        FOREIGN KEY (story_id, supersedes_event_id)
            REFERENCES timeline_events(story_id, id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_timeline_events_story_time ON timeline_events(story_id, occurred_sort_key, created_at, id)",
    """CREATE TRIGGER timeline_events_validate_insert
        BEFORE INSERT ON timeline_events
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'timeline event requires an active registered story') END;
            SELECT CASE WHEN json_array_length(NEW.payload_json, '$.claim_ids') IS NULL
                OR json_array_length(NEW.payload_json, '$.claim_ids') = 0
                OR (
                    SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.claim_ids') claim_ref
                    JOIN domain_records claim_record
                      ON claim_record.record_type = 'claim'
                     AND claim_record.id = claim_ref.value
                     AND json_extract(claim_record.payload_json, '$.story_id') = NEW.story_id
                ) != json_array_length(NEW.payload_json, '$.claim_ids')
            THEN RAISE(ABORT, 'timeline event claims must belong to its story') END;
        END""",
    """CREATE TRIGGER timeline_events_no_update
        BEFORE UPDATE ON timeline_events
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END""",
    """CREATE TRIGGER timeline_events_no_delete
        BEFORE DELETE ON timeline_events
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END""",
)


_MIGRATION_8 = (
    """CREATE TABLE perspectives (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        source_id TEXT,
        article_id TEXT,
        supersedes_perspective_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id),
        FOREIGN KEY (story_id, supersedes_perspective_id)
            REFERENCES perspectives(story_id, id) ON DELETE RESTRICT,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_perspectives_story_created ON perspectives(story_id, created_at, id)",
    """CREATE TRIGGER perspectives_validate_insert
        BEFORE INSERT ON perspectives
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'perspective requires an active registered story') END;
            SELECT CASE WHEN NEW.source_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM sources WHERE id = NEW.source_id
            ) THEN RAISE(ABORT, 'perspective source is not registered') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.kind') = 'publisher_framing'
                AND (NEW.source_id IS NULL OR NEW.article_id IS NULL)
            THEN RAISE(ABORT, 'publisher framing requires its source and article') END;
            SELECT CASE WHEN NEW.article_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM domain_records article
                WHERE article.record_type = 'article' AND article.id = NEW.article_id
                  AND json_extract(article.payload_json, '$.source_id') = NEW.source_id
                  AND EXISTS (
                    SELECT 1 FROM domain_records story
                    WHERE story.record_type = 'story' AND story.id = NEW.story_id
                      AND EXISTS (
                        SELECT 1 FROM json_each(story.payload_json, '$.article_ids') member
                        WHERE member.value = NEW.article_id
                      )
                  )
            ) THEN RAISE(ABORT, 'perspective article must belong to its source and story') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('approved', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed perspective requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('unreviewed', 'review_required')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed perspective cannot contain review metadata') END;
            SELECT CASE WHEN json_array_length(NEW.payload_json, '$.claim_ids') IS NULL
                OR (
                    SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.claim_ids') claim_ref
                    JOIN domain_records claim_record
                      ON claim_record.record_type = 'claim'
                     AND claim_record.id = claim_ref.value
                     AND json_extract(claim_record.payload_json, '$.story_id') = NEW.story_id
                ) != json_array_length(NEW.payload_json, '$.claim_ids')
            THEN RAISE(ABORT, 'perspective claims must belong to its story') END;
        END""",
    """CREATE TRIGGER perspectives_no_update
        BEFORE UPDATE ON perspectives
        BEGIN SELECT RAISE(ABORT, 'perspectives are append-only'); END""",
    """CREATE TRIGGER perspectives_no_delete
        BEFORE DELETE ON perspectives
        BEGIN SELECT RAISE(ABORT, 'perspectives are append-only'); END""",
)


_MIGRATION_9 = (
    """CREATE TABLE claim_relations (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        claim_id TEXT NOT NULL,
        related_claim_id TEXT NOT NULL,
        relationship TEXT NOT NULL CHECK (relationship IN (
            'contradicts', 'qualifies', 'narrows', 'reframes'
        )),
        supersedes_relation_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id),
        FOREIGN KEY (story_id, supersedes_relation_id)
            REFERENCES claim_relations(story_id, id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_claim_relations_story_created ON claim_relations(story_id, created_at, id)",
    """CREATE TRIGGER claim_relations_validate_insert
        BEFORE INSERT ON claim_relations
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'claim relation requires an active registered story') END;
            SELECT CASE WHEN NEW.claim_id = NEW.related_claim_id
                THEN RAISE(ABORT, 'claim relation must connect two different claims') END;
            SELECT CASE WHEN (
                SELECT COUNT(*) FROM domain_records
                WHERE record_type = 'claim' AND id IN (NEW.claim_id, NEW.related_claim_id)
                  AND json_extract(payload_json, '$.story_id') = NEW.story_id
            ) != 2
            THEN RAISE(ABORT, 'claim relation endpoints must belong to its story') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('accepted', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed claim relation requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') = 'unreviewed'
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed claim relation cannot contain review metadata') END;
        END""",
    """CREATE TRIGGER claim_relations_no_update
        BEFORE UPDATE ON claim_relations
        BEGIN SELECT RAISE(ABORT, 'claim relations are append-only'); END""",
    """CREATE TRIGGER claim_relations_no_delete
        BEFORE DELETE ON claim_relations
        BEGIN SELECT RAISE(ABORT, 'claim relations are append-only'); END""",
)


class SQLiteRegistry:
    """Transactional SQLite registry; safe for local development and low-volume use.

    The class serializes access to its connection. Separate instances can safely
    share a file database because writes use BEGIN IMMEDIATE and SQLite locking.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        if self.database != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        try:
            self.migrate()
        except Exception:
            self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteRegistry":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @contextmanager
    def _transaction(self, mode: str = "IMMEDIATE") -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute(f"BEGIN {mode}")
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                try:
                    self._connection.commit()
                except Exception:
                    # A deferred constraint or I/O error can fail at COMMIT,
                    # after the context body has returned successfully. Roll
                    # back explicitly so this connection is not left inside
                    # a failed, partially open transaction.
                    self._connection.rollback()
                    raise

    def migrate(self) -> None:
        """Apply ordered migrations atomically; reject unknown future versions."""
        with self._transaction("EXCLUSIVE") as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > _SCHEMA_VERSION:
                raise RegistryMigrationError(
                    f"Database schema {version} is newer than supported {_SCHEMA_VERSION}"
                )
            if version == 0:
                try:
                    for statement in _MIGRATION_1:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (1, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 1")
                    version = 1
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 1 failed; transaction rolled back") from exc
            if version == 1:
                try:
                    for statement in _MIGRATION_2:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (2, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 2")
                    version = 2
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 2 failed; transaction rolled back") from exc
            if version == 2 and _SCHEMA_VERSION >= 3:
                try:
                    for statement in _MIGRATION_3:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (3, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 3")
                    version = 3
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 3 failed; transaction rolled back") from exc
            if version == 3:
                try:
                    for statement in _MIGRATION_4:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (4, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 4")
                    version = 4
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 4 failed; transaction rolled back") from exc
            if version == 4:
                try:
                    for statement in _MIGRATION_5:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (5, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 5")
                    version = 5
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 5 failed; transaction rolled back") from exc

            if version == 5:
                try:
                    for statement in _MIGRATION_6:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (6, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 6")
                    version = 6
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 6 failed; transaction rolled back") from exc

            if version == 6 and _SCHEMA_VERSION >= 7:
                try:
                    for statement in _MIGRATION_7:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (7, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 7")
                    version = 7
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 7 failed; transaction rolled back") from exc
            
            if version == 7 and _SCHEMA_VERSION >= 8:
                try:
                    for statement in _MIGRATION_8:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (8, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 8")
                    version = 8
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 8 failed; transaction rolled back") from exc

            if version == 8 and _SCHEMA_VERSION >= 9:
                try:
                    for statement in _MIGRATION_9:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (9, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 9")
                    version = 9
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 9 failed; transaction rolled back") from exc

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        policy_version: str | None = None,
    ) -> str:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else None
        event_id = f"audit:{uuid.uuid4().hex}"
        occurred_at = _utc_now()
        payload_json = _json_text(payload)
        event_hash = sha256_fingerprint(
            {
                "event_id": event_id,
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "occurred_at": occurred_at,
                "policy_version": policy_version,
                "payload": json.loads(payload_json),
                "previous_hash": previous_hash,
            }
        )
        connection.execute(
            """INSERT INTO audit_events
            (event_id, event_type, entity_type, entity_id, occurred_at,
             policy_version, payload_json, previous_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                entity_type,
                entity_id,
                occurred_at,
                policy_version,
                payload_json,
                previous_hash,
                event_hash,
            ),
        )
        return event_id

    @staticmethod
    def _check_record_times(payload: dict[str, Any], start: str, end: str) -> None:
        if _aware_datetime(payload[end], end) < _aware_datetime(payload[start], start):
            raise RegistryIntegrityError(f"{end} cannot be earlier than {start}")

    def add_source(self, payload: dict[str, Any]) -> SourceRecord:
        validate_payload("source", payload)
        if not valid_domain_allowlist(payload.get("domains")):
            raise RegistryIntegrityError("source domain allowlist must contain unique canonical DNS names")
        self._check_record_times(payload, "created_at", "updated_at")
        record = SourceRecord.from_payload(payload)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO sources
                (id, publisher_name, status, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.publisher_name,
                    record.status,
                    payload["created_at"],
                    payload["updated_at"],
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type="source.created",
                entity_type="source",
                entity_id=record.id,
                payload={"record": payload},
            )
        return record

    def add_endpoint(self, payload: dict[str, Any]) -> SourceEndpointRecord:
        validate_payload("source-endpoint", payload)
        self._check_record_times(payload, "created_at", "updated_at")
        record = SourceEndpointRecord.from_payload(payload)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (record.source_id,)
            ).fetchone() is None:
                raise RegistryIntegrityError("endpoint references an unknown source")
            connection.execute(
                """INSERT INTO source_endpoints
                (id, source_id, endpoint_uri, status, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.source_id,
                    record.endpoint_uri,
                    record.status,
                    payload["created_at"],
                    payload["updated_at"],
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type="source_endpoint.created",
                entity_type="source_endpoint",
                entity_id=record.id,
                payload={"source_id": record.source_id, "record": payload},
            )
        return record

    def add_permission_evidence(
        self, payload: dict[str, Any]
    ) -> PermissionEvidenceRecord:
        validate_payload("permission-evidence", payload)
        _aware_datetime(payload["captured_at"], "captured_at")
        record = PermissionEvidenceRecord.from_payload(payload)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (record.source_id,)
            ).fetchone() is None:
                raise RegistryIntegrityError("permission evidence references an unknown source")
            connection.execute(
                """INSERT INTO permission_evidence
                (id, source_id, review_status, content_fingerprint, captured_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.source_id,
                    record.review_status,
                    record.content_fingerprint,
                    payload["captured_at"],
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type="permission_evidence.created",
                entity_type="permission_evidence",
                entity_id=record.id,
                payload={"source_id": record.source_id, "record": payload},
            )
        return record

    @staticmethod
    def _load_payload(row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["payload_json"])

    def _validate_grant_references(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        source_id = payload["source_id"]
        source = connection.execute(
            "SELECT status FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise RegistryIntegrityError("permission grant references an unknown source")

        endpoint_ids = payload["endpoint_ids"]
        placeholders = ",".join("?" for _ in endpoint_ids)
        endpoints = connection.execute(
            f"""SELECT id, status FROM source_endpoints
                WHERE source_id = ? AND id IN ({placeholders})""",
            (source_id, *endpoint_ids),
        ).fetchall()
        endpoint_map = {row["id"]: row["status"] for row in endpoints}
        missing_endpoints = sorted(set(endpoint_ids) - set(endpoint_map))
        if missing_endpoints:
            raise RegistryIntegrityError(
                "permission grant endpoint is missing or belongs to another source: "
                + ", ".join(missing_endpoints)
            )

        evidence_ids = payload["evidence_ids"]
        placeholders = ",".join("?" for _ in evidence_ids)
        evidence_rows = connection.execute(
            f"""SELECT id, review_status FROM permission_evidence
                WHERE source_id = ? AND id IN ({placeholders})""",
            (source_id, *evidence_ids),
        ).fetchall()
        evidence_map = {row["id"]: row["review_status"] for row in evidence_rows}
        missing_evidence = sorted(set(evidence_ids) - set(evidence_map))
        if missing_evidence:
            raise RegistryIntegrityError(
                "permission grant evidence is missing or belongs to another source: "
                + ", ".join(missing_evidence)
            )

        approved_grant = payload["status"] == "approved"
        if approved_grant:
            if source["status"] != "active":
                raise RegistryIntegrityError("an approved grant requires an active source")
            if any(status != "active" for status in endpoint_map.values()):
                raise RegistryIntegrityError("an approved grant requires active endpoints")
            if any(status != "verified" for status in evidence_map.values()):
                raise RegistryIntegrityError("an approved grant requires verified evidence")

        for operation, decision in payload["operations"].items():
            if decision["allowed"]:
                operation_evidence = decision.get("evidence_ids", [])
                if not set(operation_evidence).issubset(set(evidence_ids)):
                    raise RegistryIntegrityError(
                        f"{operation} evidence must also appear in grant evidence_ids"
                    )
                if any(evidence_map.get(item) != "verified" for item in operation_evidence):
                    raise RegistryIntegrityError(
                        f"{operation} requires verified, same-source evidence"
                    )

    def _insert_grant_links(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        grant_id = payload["id"]
        source_id = payload["source_id"]
        for endpoint_id in payload["endpoint_ids"]:
            connection.execute(
                "INSERT INTO grant_endpoints(grant_id, source_id, endpoint_id) VALUES (?, ?, ?)",
                (grant_id, source_id, endpoint_id),
            )
        for evidence_id in payload["evidence_ids"]:
            connection.execute(
                "INSERT INTO grant_evidence(grant_id, source_id, evidence_id) VALUES (?, ?, ?)",
                (grant_id, source_id, evidence_id),
            )
        for operation, decision in payload["operations"].items():
            for evidence_id in decision.get("evidence_ids", []):
                connection.execute(
                    """INSERT INTO grant_operation_evidence
                    (grant_id, source_id, operation, evidence_id) VALUES (?, ?, ?, ?)""",
                    (grant_id, source_id, operation, evidence_id),
                )

    def _insert_grant_revision(
        self,
        connection: sqlite3.Connection,
        *,
        grant_id: str,
        revision: int,
        payload: dict[str, Any],
        actor: str,
        reason: str,
        created_at: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO permission_grant_revisions
            (grant_id, revision, created_at, actor, reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                grant_id,
                revision,
                created_at or _utc_now(),
                actor,
                reason,
                _json_text(payload),
            ),
        )

    def add_permission_grant(self, payload: dict[str, Any]) -> PermissionGrantRecord:
        validate_payload("source-permission-grant", payload)
        starts = _aware_datetime(payload["effective_from"], "effective_from")
        ends = _aware_datetime(payload["effective_until"], "effective_until")
        if ends <= starts:
            raise RegistryIntegrityError("effective_until must be later than effective_from")
        record = PermissionGrantRecord.from_payload(payload)
        with self._transaction() as connection:
            self._validate_grant_references(connection, payload)
            connection.execute(
                """INSERT INTO permission_grants
                (id, source_id, status, effective_from, effective_until, revision, payload_json)
                VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    record.id,
                    record.source_id,
                    record.status,
                    record.effective_from,
                    record.effective_until,
                    record.payload_json,
                ),
            )
            self._insert_grant_links(connection, payload)
            self._insert_grant_revision(
                connection,
                grant_id=record.id,
                revision=1,
                payload=payload,
                actor=payload.get("reviewed_by", "system"),
                reason="grant created",
            )
            self._append_audit(
                connection,
                event_type="permission_grant.created",
                entity_type="permission_grant",
                entity_id=record.id,
                payload={"revision": 1, "record": payload},
            )
        return record

    def get_source(self, source_id: str) -> SourceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        return SourceRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def get_endpoint(self, endpoint_id: str) -> SourceEndpointRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM source_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
        return SourceEndpointRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def get_permission_evidence(self, evidence_id: str) -> PermissionEvidenceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM permission_evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        return (
            PermissionEvidenceRecord.from_payload(json.loads(row["payload_json"]))
            if row
            else None
        )

    def get_permission_grant(self, grant_id: str) -> PermissionGrantRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM permission_grants WHERE id = ?", (grant_id,)
            ).fetchone()
        return PermissionGrantRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def transition_permission_grant(
        self,
        grant_id: str,
        new_status: str,
        *,
        actor: str,
        reason: str,
        at: str | None = None,
    ) -> PermissionGrantRecord:
        """Append a grant revision for approval, denial, suspension, or revocation."""
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        if not isinstance(new_status, str):
            raise ValueError("new_status must be a string")
        allowed_transitions = {
            "pending": {"approved", "denied", "suspended", "revoked"},
            "approved": {"suspended", "revoked"},
            "suspended": {"approved", "revoked"},
            "denied": set(),
            "revoked": set(),
        }
        stamp = _utc_now() if at is None else at
        _aware_datetime(stamp, "transition timestamp")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM permission_grants WHERE id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("unknown permission grant")
            payload = self._load_payload(row)
            old_status = payload["status"]
            if new_status not in allowed_transitions.get(old_status, set()):
                raise RegistryIntegrityError(
                    f"invalid permission grant transition: {old_status} -> {new_status}"
                )
            payload["status"] = new_status
            if new_status == "approved":
                payload["reviewed_at"] = stamp
                payload["reviewed_by"] = actor
                payload.pop("revoked_at", None)
                payload.pop("revocation_reason", None)
                self._validate_grant_references(connection, payload)
            elif new_status == "revoked":
                payload["revoked_at"] = stamp
                payload["revocation_reason"] = reason
            elif new_status == "suspended":
                payload["notes"] = (payload.get("notes", "") + f"\nSuspended: {reason}").strip()
            validate_payload("source-permission-grant", payload)
            revision = row["revision"] + 1
            connection.execute(
                """UPDATE permission_grants
                SET status = ?, revision = ?, payload_json = ?
                WHERE id = ?""",
                (new_status, revision, _json_text(payload), grant_id),
            )
            self._insert_grant_revision(
                connection,
                grant_id=grant_id,
                revision=revision,
                payload=payload,
                actor=actor,
                reason=reason,
                created_at=stamp,
            )
            self._append_audit(
                connection,
                event_type="permission_grant.transitioned",
                entity_type="permission_grant",
                entity_id=grant_id,
                payload={
                    "from": old_status,
                    "to": new_status,
                    "revision": revision,
                    "actor": actor,
                    "reason": reason,
                    "record": payload,
                },
            )
        return PermissionGrantRecord.from_payload(payload)

    def evaluate_grant(
        self,
        grant_id: str,
        operation: str,
        *,
        endpoint_id: str,
        now: datetime | None = None,
    ) -> PermissionDecision:
        """Resolve and verify stored scope/evidence before evaluating a grant."""
        if not isinstance(grant_id, str) or not grant_id.strip():
            return PermissionDecision(False, "invalid_permission_id")
        evaluation_time = now if now is not None else datetime.now(timezone.utc)
        grant_revision = None
        permission_record_hash = None
        evidence_snapshot: dict[str, str] = {}
        source_status = None
        endpoint_status = None
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM permission_grants WHERE id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                decision = PermissionDecision(False, "no_permission_record", grant_id)
                source_id = None
            else:
                grant_revision = row["revision"]
                payload = self._load_payload(row)
                permission_record_hash = sha256_fingerprint(payload)
                source_id = payload["source_id"]
                source = connection.execute(
                    "SELECT status FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
                source_status = source["status"] if source else None
                endpoint = None
                if isinstance(endpoint_id, str) and endpoint_id.strip():
                    endpoint = connection.execute(
                        """SELECT status FROM source_endpoints
                        WHERE id = ? AND source_id = ?""",
                        (endpoint_id, source_id),
                    ).fetchone()
                endpoint_status = endpoint["status"] if endpoint else None
                if not isinstance(operation, str) or operation not in OPERATIONS:
                    decision = PermissionDecision(False, "unknown_operation", grant_id)
                elif not isinstance(endpoint_id, str) or not endpoint_id.strip():
                    decision = PermissionDecision(False, "endpoint_scope_mismatch", grant_id)
                elif source is None:
                    decision = PermissionDecision(False, "source_record_missing", grant_id)
                elif source["status"] != "active":
                    decision = PermissionDecision(False, "source_not_active", grant_id)
                elif endpoint is None or endpoint_id not in payload.get("endpoint_ids", []):
                    decision = PermissionDecision(False, "endpoint_scope_mismatch", grant_id)
                elif endpoint["status"] != "active":
                    decision = PermissionDecision(False, "endpoint_not_active", grant_id)
                else:
                    all_evidence = payload.get("evidence_ids", [])
                    evidence_map: dict[str, str] = {}
                    for evidence_id in all_evidence:
                        evidence = connection.execute(
                            """SELECT review_status FROM permission_evidence
                            WHERE id = ? AND source_id = ?""",
                            (evidence_id, source_id),
                        ).fetchone()
                        if evidence is not None:
                            evidence_map[evidence_id] = evidence["review_status"]
                    evidence_snapshot = dict(evidence_map)
                    if len(evidence_map) != len(set(all_evidence)):
                        decision = PermissionDecision(
                            False, "permission_evidence_missing", grant_id
                        )
                    elif any(status != "verified" for status in evidence_map.values()) and payload.get("status") == "approved":
                        decision = PermissionDecision(
                            False, "permission_evidence_not_verified", grant_id
                        )
                    else:
                        operation_record = payload.get("operations", {}).get(operation, {})
                        operation_evidence = operation_record.get("evidence_ids", [])
                        if operation_record.get("allowed") is True and any(
                            evidence_map.get(item) != "verified" for item in operation_evidence
                        ):
                            decision = PermissionDecision(
                                False, "operation_evidence_not_verified", grant_id
                            )
                        else:
                            decision = evaluate_permission_record(
                                payload,
                                operation,
                                source_id=source_id,
                                endpoint_id=endpoint_id,
                                now=evaluation_time,
                            )
            self._append_audit(
                connection,
                event_type="permission.decision",
                entity_type="permission_grant",
                entity_id=grant_id,
                policy_version=POLICY_VERSION,
                payload={
                    "operation": operation,
                    "source_id": source_id,
                    "endpoint_id": endpoint_id,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "permission_id": decision.permission_id,
                    "grant_revision": grant_revision,
                    "permission_record_hash": permission_record_hash,
                    "source_status": source_status,
                    "endpoint_status": endpoint_status,
                    "evidence_statuses": evidence_snapshot,
                    "evaluated_at": (
                        evaluation_time.isoformat()
                        if isinstance(evaluation_time, datetime)
                        else str(evaluation_time)
                    ),
                },
            )
        if grant_revision is not None:
            decision = PermissionDecision(
                decision.allowed,
                decision.reason,
                decision.permission_id,
                grant_revision,
            )
        return decision

    def grant_history(self, grant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM permission_grant_revisions WHERE grant_id = ?
                ORDER BY revision""",
                (grant_id,),
            ).fetchall()
        return [
            {
                "revision": row["revision"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def append_many(self, observations: tuple[Any, ...]) -> None:
        """Persist a batch only while its exact permission grant remains current."""
        payloads: list[dict[str, Any]] = []
        for observation in observations:
            if is_dataclass(observation):
                payload = asdict(observation)
            elif isinstance(observation, dict):
                payload = dict(observation)
            else:
                raise RegistryIntegrityError("collection observation must be a dataclass or object")
            payloads.append(payload)
        if not payloads:
            return

        required = {
            "id", "source_id", "endpoint_id", "grant_id", "grant_revision",
            "operation", "connector_name", "connector_version", "observed_at",
            "locator", "final_locator", "content_fingerprint", "response_status",
            "content_type", "metadata", "permission_reason", "http_method",
        }
        for payload in payloads:
            if set(payload) != required:
                raise RegistryIntegrityError("collection observation fields are incomplete or unexpected")
            if payload["operation"] not in {"discovery", "metadata"}:
                raise RegistryIntegrityError("collection operation is not implemented")
            if not isinstance(payload["grant_revision"], int) or isinstance(payload["grant_revision"], bool):
                raise RegistryIntegrityError("collection observation requires a grant revision")
            for field in ("id", "source_id", "endpoint_id", "grant_id", "connector_name", "connector_version"):
                if not isinstance(payload[field], str) or not payload[field].strip():
                    raise RegistryIntegrityError(f"collection {field} is required")
            if not isinstance(payload["metadata"], dict):
                raise RegistryIntegrityError("collection metadata must be an object")
            metadata_keys = {"title", "byline", "published_at", "summary_metadata"}
            if set(payload["metadata"]) - metadata_keys:
                raise RegistryIntegrityError("collection metadata contains unsupported fields")
            if any(
                not isinstance(value, str) or len(value) > 1000
                for value in payload["metadata"].values()
            ):
                raise RegistryIntegrityError("collection metadata values must be bounded strings")
            if payload["operation"] == "discovery" and payload["metadata"]:
                raise RegistryIntegrityError("discovery observations cannot retain article metadata")
            for uri_field in ("locator", "final_locator"):
                uri = payload[uri_field]
                if uri is None and uri_field == "final_locator":
                    continue
                if not valid_http_uri(uri, max_length=2048):
                    raise RegistryIntegrityError(
                        f"collection {uri_field} must be a safe absolute HTTP(S) URI"
                    )
            fingerprint = payload["content_fingerprint"]
            if fingerprint is not None and (
                not isinstance(fingerprint, str)
                or not fingerprint.startswith("sha256:")
                or len(fingerprint) != 71
            ):
                raise RegistryIntegrityError("collection fingerprint must be a SHA-256 fingerprint")
            status = payload["response_status"]
            if status is not None and (
                isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599
            ):
                raise RegistryIntegrityError("collection response status is invalid")
            content_type = payload["content_type"]
            if content_type is not None and (
                not isinstance(content_type, str) or len(content_type) > 160
            ):
                raise RegistryIntegrityError("collection content type is invalid")
            if payload["http_method"] != "GET":
                raise RegistryIntegrityError("collection observations currently support GET only")
            if payload["permission_reason"] != "approved":
                raise RegistryIntegrityError("collection observation lacks an approved permission decision")
            _aware_datetime(payload["observed_at"], "observed_at")

        with self._transaction() as connection:
            for payload in payloads:
                source = connection.execute(
                    "SELECT status, payload_json FROM sources WHERE id = ?",
                    (payload["source_id"],),
                ).fetchone()
                endpoint = connection.execute(
                    """SELECT status, endpoint_uri FROM source_endpoints
                    WHERE id = ? AND source_id = ?""",
                    (payload["endpoint_id"], payload["source_id"]),
                ).fetchone()
                grant = connection.execute(
                    """SELECT revision, payload_json FROM permission_grants
                    WHERE id = ? AND source_id = ?""",
                    (payload["grant_id"], payload["source_id"]),
                ).fetchone()
                if source is None or source["status"] != "active":
                    raise RegistryIntegrityError("collection source is missing or inactive")
                if endpoint is None or endpoint["status"] != "active":
                    raise RegistryIntegrityError("collection endpoint is missing or inactive")
                allowed_domains = json.loads(source["payload_json"]).get("domains", [])
                if not valid_domain_allowlist(allowed_domains):
                    raise RegistryIntegrityError("collection source domain allowlist is invalid")
                endpoint_host = urlsplit(endpoint["endpoint_uri"]).hostname or ""
                if not _host_in_domains(endpoint_host, allowed_domains):
                    raise RegistryIntegrityError("collection endpoint is outside the registered source domains")
                for uri_field in ("locator", "final_locator"):
                    uri_value = payload[uri_field]
                    if uri_value is None:
                        continue
                    uri_host = urlsplit(uri_value).hostname or ""
                    if not _host_in_domains(uri_host, allowed_domains):
                        raise RegistryIntegrityError(
                            f"collection {uri_field} is outside the registered source domains"
                        )
                if grant is None:
                    raise RegistryIntegrityError("collection grant is missing or belongs to another source")
                if grant["revision"] != payload["grant_revision"]:
                    raise RegistryIntegrityError("permission grant changed during collection")
                grant_payload = json.loads(grant["payload_json"])
                if grant_payload["status"] != "approved":
                    raise RegistryIntegrityError("permission grant is no longer approved")

                evidence_ids = grant_payload.get("evidence_ids", [])
                evidence_statuses: dict[str, str] = {}
                for evidence_id in evidence_ids:
                    evidence = connection.execute(
                        """SELECT review_status FROM permission_evidence
                        WHERE id = ? AND source_id = ?""",
                        (evidence_id, payload["source_id"]),
                    ).fetchone()
                    if evidence is not None:
                        evidence_statuses[evidence_id] = evidence["review_status"]
                if len(evidence_statuses) != len(set(evidence_ids)) or any(
                    status != "verified" for status in evidence_statuses.values()
                ):
                    raise RegistryIntegrityError("collection permission evidence is no longer verified")

                decision = evaluate_permission_record(
                    grant_payload,
                    payload["operation"],
                    source_id=payload["source_id"],
                    endpoint_id=payload["endpoint_id"],
                    now=datetime.now(timezone.utc),
                )
                if not decision.allowed or payload["permission_reason"] != decision.reason:
                    raise RegistryIntegrityError("permission is not valid at observation storage time")

                metadata_json = _json_text(payload["metadata"])
                connection.execute(
                    """INSERT INTO collection_observations
                    (observation_id, source_id, endpoint_id, grant_id, grant_revision,
                     operation, connector_name, connector_version, observed_at, locator,
                     final_locator, content_fingerprint, response_status, content_type,
                     metadata_json, permission_reason, http_method)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["id"], payload["source_id"], payload["endpoint_id"],
                        payload["grant_id"], payload["grant_revision"], payload["operation"],
                        payload["connector_name"], payload["connector_version"],
                        payload["observed_at"], payload["locator"], payload["final_locator"],
                        payload["content_fingerprint"], payload["response_status"],
                        payload["content_type"], metadata_json, payload["permission_reason"],
                        payload["http_method"],
                    ),
                )
                self._append_audit(
                    connection,
                    event_type="collection.observation_recorded",
                    entity_type="collection_observation",
                    entity_id=payload["id"],
                    payload={
                        "source_id": payload["source_id"],
                        "endpoint_id": payload["endpoint_id"],
                        "grant_id": payload["grant_id"],
                        "grant_revision": payload["grant_revision"],
                        "operation": payload["operation"],
                        "http_method": payload["http_method"],
                        "locator": payload["locator"],
                        "content_fingerprint": payload["content_fingerprint"],
                    },
                    policy_version=POLICY_VERSION,
                )

    def get_collection_observation(self, observation_id: str) -> dict[str, Any] | None:
        """Return one stored collection observation with its original metadata."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM collection_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["observation_id"],
            "source_id": row["source_id"],
            "endpoint_id": row["endpoint_id"],
            "grant_id": row["grant_id"],
            "grant_revision": row["grant_revision"],
            "operation": row["operation"],
            "connector_name": row["connector_name"],
            "connector_version": row["connector_version"],
            "observed_at": row["observed_at"],
            "locator": row["locator"],
            "final_locator": row["final_locator"],
            "content_fingerprint": row["content_fingerprint"],
            "response_status": row["response_status"],
            "content_type": row["content_type"],
            "metadata": json.loads(row["metadata_json"]),
            "permission_reason": row["permission_reason"],
            "http_method": row["http_method"],
        }

    @staticmethod
    def _ingestion_decision_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "decision_id": row["decision_id"],
            "observation_id": row["observation_id"],
            "decision_revision": row["decision_revision"],
            "source_id": row["source_id"],
            "article_id": row["article_id"],
            "outcome": row["outcome"],
            "duplicate_of_article_id": row["duplicate_of_article_id"],
            "fingerprint": row["fingerprint"],
            "canonical_uri": row["canonical_uri"],
            "reason": row["reason"],
            "decided_at": row["decided_at"],
        }

    def get_ingestion_decision(self, observation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE c.observation_id = ?""",
                (observation_id,),
            ).fetchone()
        return self._ingestion_decision_payload(row) if row else None

    def ingestion_decision_history(self, observation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM ingestion_decisions
                WHERE observation_id = ? ORDER BY decision_revision""",
                (observation_id,),
            ).fetchall()
        return [self._ingestion_decision_payload(row) for row in rows]

    def _check_ingestion_permission(
        self, connection: sqlite3.Connection, observation: sqlite3.Row
    ) -> None:
        if observation["operation"] != "metadata" or observation["http_method"] != "GET":
            raise RegistryIntegrityError("only GET metadata observations can be ingested")
        if observation["permission_reason"] != "approved":
            raise RegistryIntegrityError("observation lacks an approved permission decision")
        source = connection.execute(
            "SELECT status FROM sources WHERE id = ?", (observation["source_id"],)
        ).fetchone()
        endpoint = connection.execute(
            """SELECT status, payload_json FROM source_endpoints
            WHERE id = ? AND source_id = ?""",
            (observation["endpoint_id"], observation["source_id"]),
        ).fetchone()
        grant = connection.execute(
            """SELECT revision, payload_json FROM permission_grants
            WHERE id = ? AND source_id = ?""",
            (observation["grant_id"], observation["source_id"]),
        ).fetchone()
        if source is None or source["status"] != "active":
            raise RegistryIntegrityError("ingestion source is missing or inactive")
        if endpoint is None or endpoint["status"] != "active":
            raise RegistryIntegrityError("ingestion endpoint is missing or inactive")
        if grant is None or grant["revision"] != observation["grant_revision"]:
            raise RegistryIntegrityError("permission grant changed since the observation")
        source_row = connection.execute(
            "SELECT payload_json FROM sources WHERE id = ?", (observation["source_id"],)
        ).fetchone()
        source_domains = json.loads(source_row["payload_json"]).get("domains", [])
        if not valid_domain_allowlist(source_domains):
            raise RegistryIntegrityError("ingestion source domain allowlist is invalid")
        for locator in (observation["locator"], observation["final_locator"]):
            if locator is None:
                continue
            if not valid_http_uri(locator, max_length=2048):
                raise RegistryIntegrityError("ingestion observation locator is malformed")
            host = urlsplit(locator).hostname or ""
            if not _host_in_domains(host, source_domains):
                raise RegistryIntegrityError("ingestion observation locator is outside source scope")
        grant_payload = json.loads(grant["payload_json"])
        if grant_payload.get("status") != "approved":
            raise RegistryIntegrityError("permission grant is no longer approved")
        evidence_ids = grant_payload.get("evidence_ids", [])
        evidence_statuses: dict[str, str] = {}
        for evidence_id in evidence_ids:
            evidence = connection.execute(
                """SELECT review_status FROM permission_evidence
                WHERE id = ? AND source_id = ?""",
                (evidence_id, observation["source_id"]),
            ).fetchone()
            if evidence is not None:
                evidence_statuses[evidence_id] = evidence["review_status"]
        if len(evidence_statuses) != len(set(evidence_ids)) or any(
            status != "verified" for status in evidence_statuses.values()
        ):
            raise RegistryIntegrityError("ingestion permission evidence is no longer verified")
        metadata_operation = grant_payload.get("operations", {}).get("metadata", {})
        operation_evidence = metadata_operation.get("evidence_ids", [])
        if metadata_operation.get("allowed") is not True or any(
            evidence_statuses.get(item) != "verified" for item in operation_evidence
        ):
            raise RegistryIntegrityError("metadata permission evidence is no longer verified")
        decision = evaluate_permission_record(
            grant_payload,
            "metadata",
            source_id=observation["source_id"],
            endpoint_id=observation["endpoint_id"],
            now=datetime.now(timezone.utc),
        )
        if not decision.allowed:
            raise RegistryIntegrityError("metadata permission is no longer valid")

    def ingest_article_observation(
        self,
        observation_id: str,
        article_payload: dict[str, Any],
        *,
        duplicate_fingerprint: str | None,
    ) -> dict[str, Any]:
        """Atomically create/reuse an article and record an explainable identity decision."""
        validate_payload("article", article_payload)
        if duplicate_fingerprint is not None and (
            not isinstance(duplicate_fingerprint, str)
            or not duplicate_fingerprint.startswith("sha256:")
            or len(duplicate_fingerprint) != 71
            or any(char not in "0123456789abcdef" for char in duplicate_fingerprint[7:])
        ):
            raise RegistryIntegrityError("ingestion duplicate fingerprint must be SHA-256")
        with self._transaction() as connection:
            existing_decision = connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE c.observation_id = ?""",
                (observation_id,),
            ).fetchone()
            if existing_decision is not None:
                return self._ingestion_decision_payload(existing_decision)

            observation = connection.execute(
                "SELECT * FROM collection_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
            if observation is None:
                raise RegistryIntegrityError("collection observation does not exist")
            if (
                article_payload.get("source_id") != observation["source_id"]
                or article_payload.get("permission_id") != observation["grant_id"]
            ):
                raise RegistryIntegrityError("article source/permission does not match its observation")
            from .ingestion import language_is_supported, normalize_observation
            expected_payload, expected_fingerprint = normalize_observation(
                self.get_collection_observation(observation_id),
                article_payload.get("source_language", ""),
            )
            if article_payload != expected_payload or duplicate_fingerprint != expected_fingerprint:
                raise RegistryIntegrityError(
                    "article payload and duplicate fingerprint do not match normalized source observation"
                )
            self._check_ingestion_permission(connection, observation)
            endpoint_payload = json.loads(connection.execute(
                "SELECT payload_json FROM source_endpoints WHERE id = ?",
                (observation["endpoint_id"],),
            ).fetchone()["payload_json"])
            declared_languages = endpoint_payload.get("supported_languages", [])
            article_language = article_payload.get("source_language", "")
            try:
                language_supported = language_is_supported(article_language, declared_languages)
            except ValueError as exc:
                raise RegistryIntegrityError("endpoint language declaration is invalid") from exc
            if not language_supported:
                raise RegistryIntegrityError("article language is outside the endpoint language declaration")

            existing_article = connection.execute(
                """SELECT id, revision, payload_json FROM domain_records
                WHERE record_type = 'article'
                  AND json_extract(payload_json, '$.source_id') = ?
                  AND json_extract(payload_json, '$.canonical_uri') = ?
                ORDER BY created_at, id LIMIT 1""",
                (article_payload["source_id"], article_payload["canonical_uri"]),
            ).fetchone()

            if existing_article is not None:
                article_id = existing_article["id"]
                outcome = "reused"
                existing_payload = json.loads(existing_article["payload_json"])
                incoming_time = _aware_datetime(article_payload["updated_at"], "updated_at")
                existing_time = _aware_datetime(
                    existing_payload.get("updated_at", existing_payload["discovered_at"]),
                    "updated_at",
                )
                if incoming_time > existing_time:
                    refreshed = dict(existing_payload)
                    for field in (
                        "title", "source_language", "updated_at", "provenance",
                        "byline", "published_at", "summary_metadata", "content_fingerprint",
                    ):
                        if field in article_payload:
                            refreshed[field] = article_payload[field]
                    validate_payload("article", refreshed)
                    next_revision = existing_article["revision"] + 1
                    refreshed_json = _json_text(refreshed)
                    connection.execute(
                        """UPDATE domain_records
                        SET revision = ?, updated_at = ?, payload_json = ?
                        WHERE record_type = 'article' AND id = ? AND revision = ?""",
                        (
                            next_revision, article_payload["updated_at"], refreshed_json,
                            article_id, existing_article["revision"],
                        ),
                    )
                    connection.execute(
                        """INSERT INTO domain_record_revisions
                        (record_type, record_id, revision, created_at, actor, reason, payload_json)
                        VALUES ('article', ?, ?, ?, 'ingestion', 'metadata refreshed from newer observation', ?)""",
                        (article_id, next_revision, _utc_now(), refreshed_json),
                    )
                    self._append_audit(
                        connection,
                        event_type="article.revised",
                        entity_type="article",
                        entity_id=article_id,
                        payload={
                            "revision": next_revision,
                            "actor": "ingestion",
                            "reason": "metadata refreshed from newer observation",
                            "observation_id": observation_id,
                        },
                    )
                    reason = "same_source_article_reused_and_metadata_refreshed"
                else:
                    reason = "same_source_canonical_uri_already_exists_newer_or_equal_metadata_retained"
            else:
                self._validate_domain_references(connection, "article", article_payload)
                article_json = _json_text(article_payload)
                created_at = article_payload["discovered_at"]
                connection.execute(
                    """INSERT INTO domain_records
                    (record_type, id, revision, created_at, updated_at, payload_json)
                    VALUES ('article', ?, 1, ?, ?, ?)""",
                    (article_payload["id"], created_at, _utc_now(), article_json),
                )
                connection.execute(
                    """INSERT INTO domain_record_revisions
                    (record_type, record_id, revision, created_at, actor, reason, payload_json)
                    VALUES ('article', ?, 1, ?, 'ingestion', 'article ingested from observation', ?)""",
                    (article_payload["id"], _utc_now(), article_json),
                )
                article_id = article_payload["id"]
                outcome = "created"
                reason = "new_source_article_identity"

            duplicate_of = None
            if outcome == "created" and duplicate_fingerprint is not None:
                candidate = connection.execute(
                    """SELECT d.article_id FROM ingestion_current c
                    JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                    JOIN domain_records a ON a.record_type = 'article' AND a.id = d.article_id
                    WHERE d.fingerprint = ? AND d.source_id != ?
                      AND d.outcome IN ('created', 'reused', 'distinct')
                      AND json_extract(a.payload_json, '$.record_status') = 'active'
                      AND NOT EXISTS (
                          SELECT 1 FROM ingestion_current c2
                          JOIN ingestion_decisions d2 ON d2.decision_id = c2.decision_id
                          WHERE d2.article_id = d.article_id
                            AND (
                                d2.decided_at > d.decided_at
                                OR (d2.decided_at = d.decided_at AND d2.decision_id > d.decision_id)
                            )
                      )
                    ORDER BY d.decided_at, d.decision_id LIMIT 1""",
                    (duplicate_fingerprint, article_payload["source_id"]),
                ).fetchone()
                if candidate is not None and candidate["article_id"] != article_id:
                    outcome = "possible_duplicate"
                    duplicate_of = candidate["article_id"]
                    reason = "matching_normalized_title_language_and_publication_time"

            decision_id = f"ingestion-decision:{uuid.uuid4().hex}"
            decided_at = _utc_now()
            connection.execute(
                """INSERT INTO ingestion_decisions
                (decision_id, observation_id, decision_revision, source_id, article_id,
                 outcome, duplicate_of_article_id, fingerprint, canonical_uri, reason, decided_at)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id, observation_id, article_payload["source_id"], article_id,
                    outcome, duplicate_of, duplicate_fingerprint,
                    article_payload["canonical_uri"], reason, decided_at,
                ),
            )
            connection.execute(
                "INSERT INTO ingestion_current(observation_id, decision_id) VALUES (?, ?)",
                (observation_id, decision_id),
            )
            self._append_audit(
                connection,
                event_type="ingestion.decided",
                entity_type="ingestion_decision",
                entity_id=decision_id,
                payload={
                    "observation_id": observation_id,
                    "article_id": article_id,
                    "outcome": outcome,
                    "duplicate_of_article_id": duplicate_of,
                    "fingerprint": duplicate_fingerprint,
                    "reason": reason,
                    "decision_revision": 1,
                },
            )
            row = connection.execute(
                "SELECT * FROM ingestion_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._ingestion_decision_payload(row)

    def resolve_ingestion_duplicate(
        self,
        observation_id: str,
        *,
        outcome: str,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Append a human resolution; never merges or deletes either article."""
        if not isinstance(outcome, str) or outcome not in {"confirmed_duplicate", "distinct"}:
            raise ValueError("outcome must be confirmed_duplicate or distinct")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 800:
            raise ValueError("a bounded resolution reason is required")
        if len(actor) > 200 or len(actor) + 2 + len(reason) > 1000:
            raise ValueError("actor and reason exceed the 1000-character audit limit")
        with self._transaction() as connection:
            current = connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE c.observation_id = ?""",
                (observation_id,),
            ).fetchone()
            if current is None:
                raise RegistryIntegrityError("ingestion decision does not exist")
            if current["decision_revision"] != expected_revision:
                raise RegistryIntegrityError("ingestion decision revision conflict")
            if current["outcome"] not in {"possible_duplicate", "confirmed_duplicate", "distinct"}:
                raise RegistryIntegrityError("ingestion decision is not a duplicate candidate")
            duplicate_of = current["duplicate_of_article_id"]
            if duplicate_of is None:
                prior_candidate = connection.execute(
                    """SELECT duplicate_of_article_id FROM ingestion_decisions
                    WHERE observation_id = ? AND duplicate_of_article_id IS NOT NULL
                    ORDER BY decision_revision DESC LIMIT 1""",
                    (observation_id,),
                ).fetchone()
                duplicate_of = prior_candidate["duplicate_of_article_id"] if prior_candidate else None
            if outcome == "confirmed_duplicate" and duplicate_of is None:
                raise RegistryIntegrityError("no duplicate candidate is available to confirm")
            decision_id = f"ingestion-decision:{uuid.uuid4().hex}"
            decided_at = _utc_now()
            next_revision = expected_revision + 1
            connection.execute(
                """INSERT INTO ingestion_decisions
                (decision_id, observation_id, decision_revision, source_id, article_id,
                 outcome, duplicate_of_article_id, fingerprint, canonical_uri, reason, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id, observation_id, next_revision, current["source_id"],
                    current["article_id"], outcome,
                    duplicate_of if outcome == "confirmed_duplicate" else None,
                    current["fingerprint"], current["canonical_uri"],
                    f"{actor}: {reason}", decided_at,
                ),
            )
            connection.execute(
                """UPDATE ingestion_current SET decision_id = ?
                WHERE observation_id = ? AND decision_id = ?""",
                (decision_id, observation_id, current["decision_id"]),
            )
            self._append_audit(
                connection,
                event_type="ingestion.duplicate_resolved",
                entity_type="ingestion_decision",
                entity_id=decision_id,
                payload={
                    "observation_id": observation_id,
                    "article_id": current["article_id"],
                    "previous_decision_id": current["decision_id"],
                    "outcome": outcome,
                    "duplicate_of_article_id": duplicate_of if outcome == "confirmed_duplicate" else None,
                    "actor": actor,
                    "reason": reason,
                    "decision_revision": next_revision,
                },
            )
            row = connection.execute(
                "SELECT * FROM ingestion_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._ingestion_decision_payload(row)

    def collection_observations(
        self, *, source_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM collection_observations"
        params: tuple[Any, ...] = ()
        if source_id is not None:
            query += " WHERE source_id = ?"
            params = (source_id,)
        query += " ORDER BY observed_at, observation_id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [
            {
                "id": row["observation_id"],
                "source_id": row["source_id"],
                "endpoint_id": row["endpoint_id"],
                "grant_id": row["grant_id"],
                "grant_revision": row["grant_revision"],
                "operation": row["operation"],
                "connector_name": row["connector_name"],
                "connector_version": row["connector_version"],
                "observed_at": row["observed_at"],
                "locator": row["locator"],
                "final_locator": row["final_locator"],
                "content_fingerprint": row["content_fingerprint"],
                "response_status": row["response_status"],
                "content_type": row["content_type"],
                "metadata": json.loads(row["metadata_json"]),
                "permission_reason": row["permission_reason"],
                "http_method": row["http_method"],
            }
            for row in rows
        ]

    def audit_events(self, *, entity_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        params: tuple[Any, ...] = ()
        if entity_id is not None:
            query += " WHERE entity_id = ?"
            params = (entity_id,)
        query += " ORDER BY sequence"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "occurred_at": row["occurred_at"],
                "policy_version": row["policy_version"],
                "payload": json.loads(row["payload_json"]),
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
            }
            for row in rows
        ]

    def audit_checkpoint(self) -> dict[str, Any]:
        """Return the current chain tip for storage outside this database.

        Persist this sequence/hash pair in an independently controlled system
        if later verification must detect deletion or truncation of the chain tail.
        A checkpoint kept only in this database provides no external protection.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"sequence": 0, "event_hash": None}
        return {"sequence": row["sequence"], "event_hash": row["event_hash"]}

    def verify_audit_chain(self) -> bool:
        """Verify links from the first retained event; this cannot detect tail truncation."""
        events = self.audit_events()
        previous_hash = None
        for event in events:
            expected = sha256_fingerprint(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "entity_type": event["entity_type"],
                    "entity_id": event["entity_id"],
                    "occurred_at": event["occurred_at"],
                    "policy_version": event["policy_version"],
                    "payload": event["payload"],
                    "previous_hash": previous_hash,
                }
            )
            if event["previous_hash"] != previous_hash or event["event_hash"] != expected:
                return False
            previous_hash = event["event_hash"]
        return True

    _DOMAIN_MODELS = {
        "article": ArticleRecord,
        "story": StoryRecord,
        "story_decision": StoryDecisionRecord,
        "claim": ClaimRecord,
        "evidence": EvidenceRecord,
        "edition": EditionRecord,
    }

    def _validate_domain_references(
        self, connection: sqlite3.Connection, record_type: str, payload: dict[str, Any]
    ) -> None:
        def require_record(kind: str, record_id: str, label: str) -> dict[str, Any]:
            row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = ? AND id = ?",
                (kind, record_id),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError(f"{label} references an unknown {kind}: {record_id}")
            return json.loads(row["payload_json"])

        if record_type == "article":
            source = connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (payload["source_id"],)
            ).fetchone()
            if source is None:
                raise RegistryIntegrityError("article references an unknown source")
            permission_id = payload.get("permission_id")
            if permission_id:
                grant = connection.execute(
                    "SELECT source_id FROM permission_grants WHERE id = ?", (permission_id,)
                ).fetchone()
                if grant is None or grant["source_id"] != payload["source_id"]:
                    raise RegistryIntegrityError(
                        "article permission reference is missing or belongs to another source"
                    )
        elif record_type == "story":
            article_ids = set(payload.get("article_ids", []))
            for article_id in article_ids:
                require_record("article", article_id, "story")
            existing_claims = connection.execute(
                """SELECT payload_json FROM domain_records
                WHERE record_type = 'claim'
                  AND json_extract(payload_json, '$.story_id') = ?""",
                (payload["id"],),
            ).fetchall()
            for row in existing_claims:
                claim_payload = json.loads(row["payload_json"])
                if any(
                    item.get("article_id") is not None
                    and item["article_id"] not in article_ids
                    for item in claim_payload.get("provenance", [])
                ):
                    raise RegistryIntegrityError(
                        "story revision would orphan existing claim provenance"
                    )
        elif record_type == "claim":
            story_payload = require_record("story", payload["story_id"], "claim")
            story_article_ids = set(story_payload.get("article_ids", []))
            for provenance in payload["provenance"]:
                source_id = provenance["source_id"]
                source = connection.execute(
                    "SELECT 1 FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
                if source is None:
                    raise RegistryIntegrityError("claim provenance references an unknown source")
                article_id = provenance.get("article_id")
                observation_id = provenance.get("observation_id")
                if article_id:
                    article = require_record("article", article_id, "claim provenance")
                    if article["source_id"] != source_id:
                        raise RegistryIntegrityError(
                            "claim provenance article and source do not match"
                        )
                    if article_id not in story_article_ids:
                        raise RegistryIntegrityError(
                            "claim provenance article is not linked to its story"
                        )
                if observation_id:
                    observation = connection.execute(
                        "SELECT source_id FROM collection_observations WHERE observation_id = ?",
                        (observation_id,),
                    ).fetchone()
                    if observation is None or observation["source_id"] != source_id:
                        raise RegistryIntegrityError(
                            "claim provenance observation is missing or belongs to another source"
                        )
                if article_id and observation_id:
                    linked = connection.execute(
                        """SELECT 1 FROM ingestion_current c
                        JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                        WHERE c.observation_id = ? AND d.article_id = ?""",
                        (observation_id, article_id),
                    ).fetchone()
                    if linked is None:
                        raise RegistryIntegrityError(
                            "claim provenance article and observation are not linked by ingestion"
                        )
            existing_evidence = connection.execute(
                """SELECT id, payload_json FROM domain_records
                WHERE record_type = 'evidence'
                  AND json_extract(payload_json, '$.claim_id') = ?""",
                (payload["id"],),
            ).fetchall()
            for row in existing_evidence:
                evidence_payload = json.loads(row["payload_json"])
                evidence_source = evidence_payload["source_id"]
                evidence_article = evidence_payload.get("article_id")
                evidence_observation = evidence_payload.get("observation_id")
                if not any(
                    item.get("source_id") == evidence_source
                    and (evidence_article is None or item.get("article_id") == evidence_article)
                    and (evidence_observation is None or item.get("observation_id") == evidence_observation)
                    for item in payload.get("provenance", [])
                ):
                    raise RegistryIntegrityError(
                        "claim revision would orphan existing evidence provenance"
                    )
            linked_evidence = []
            for evidence_id in payload.get("evidence_ids", []):
                evidence = require_record("evidence", evidence_id, "claim evidence")
                if evidence["claim_id"] != payload["id"]:
                    raise RegistryIntegrityError(
                        "claim evidence reference belongs to another claim"
                    )
                linked_evidence.append(evidence)
            assessment = payload["assessment"]
            relationships = {item["relationship"] for item in linked_evidence}
            if assessment != "unassessed":
                if not linked_evidence:
                    raise RegistryIntegrityError(
                        "an assessed claim must reference at least one evidence record"
                    )
                expected_evidence_ids = {row["id"] for row in existing_evidence}
                if set(payload.get("evidence_ids", [])) != expected_evidence_ids:
                    raise RegistryIntegrityError(
                        "reviewed claim must reference every evidence record linked to it"
                    )
                reviewed_at = payload.get("reviewed_at")
                review_note = payload.get("review_note")
                if not isinstance(reviewed_at, str) or not isinstance(review_note, str) or not review_note.strip():
                    raise RegistryIntegrityError(
                        "reviewed claim assessment requires a review timestamp and a non-empty review note (review_note)"
                    )
                if _aware_datetime(reviewed_at, "reviewed_at") < _aware_datetime(payload["created_at"], "created_at"):
                    raise RegistryIntegrityError("claim review cannot predate claim creation")
            if assessment == "supported" and (
                "supports" not in relationships or "contradicts" in relationships
            ):
                raise RegistryIntegrityError(
                    "supported claim requires supporting evidence and no unresolved contradiction"
                )
            if assessment == "partially_supported" and not (
                "supports" in relationships and ({"qualifies", "unclear"} & relationships)
            ):
                raise RegistryIntegrityError(
                    "partially supported claim requires support and qualifying/unclear evidence"
                )
            if assessment == "disputed" and not (
                "supports" in relationships and "contradicts" in relationships
            ):
                raise RegistryIntegrityError(
                    "disputed claim requires both supporting and contradicting evidence"
                )
            if assessment == "contradicted" and (
                "contradicts" not in relationships or "supports" in relationships
            ):
                raise RegistryIntegrityError(
                    "contradicted claim requires contradicting evidence without supporting evidence"
                )
            if assessment == "retracted" and not (
                isinstance(payload.get("review_note"), str) and payload["review_note"].strip()
            ):
                raise RegistryIntegrityError("retracted claim requires a non-empty review note")
        elif record_type == "evidence":
            claim = require_record("claim", payload["claim_id"], "evidence")
            if claim.get("assessment") != "unassessed":
                raise RegistryIntegrityError(
                    "claim must be reset to unassessed before adding new evidence"
                )
            source_id = payload["source_id"]
            source = connection.execute(
                "SELECT payload_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise RegistryIntegrityError("evidence references an unknown source")
            source_domains = json.loads(source["payload_json"]).get("domains", [])
            if not valid_domain_allowlist(source_domains):
                raise RegistryIntegrityError("evidence source domain allowlist is invalid")
            article_id = payload.get("article_id")
            observation_id = payload.get("observation_id")
            if article_id:
                article = require_record("article", article_id, "evidence")
                if article["source_id"] != source_id:
                    raise RegistryIntegrityError(
                        "evidence article and source do not match"
                    )
            if observation_id:
                observation = connection.execute(
                    "SELECT source_id FROM collection_observations WHERE observation_id = ?",
                    (observation_id,),
                ).fetchone()
                if observation is None or observation["source_id"] != source_id:
                    raise RegistryIntegrityError(
                        "evidence observation is missing or belongs to another source"
                    )
            if article_id and observation_id:
                linked = connection.execute(
                    """SELECT 1 FROM ingestion_current c
                    JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                    WHERE c.observation_id = ? AND d.article_id = ?""",
                    (observation_id, article_id),
                ).fetchone()
                if linked is None:
                    raise RegistryIntegrityError(
                        "evidence article and observation are not linked by ingestion"
                    )
            provenance_matches = any(
                item.get("source_id") == source_id
                and (article_id is None or item.get("article_id") == article_id)
                and (observation_id is None or item.get("observation_id") == observation_id)
                for item in claim.get("provenance", [])
            )
            if not provenance_matches:
                raise RegistryIntegrityError(
                    "evidence source/article/observation is not present in claim provenance"
                )
            locator_uri = payload["locator"]["uri"]
            try:
                parsed_locator = urlsplit(locator_uri)
            except ValueError as exc:
                raise RegistryIntegrityError("evidence locator is malformed") from exc
            if parsed_locator.scheme.lower() not in {"http", "https"} or not parsed_locator.hostname:
                raise RegistryIntegrityError("evidence locator must be an absolute HTTP(S) URL")
            if parsed_locator.username is not None or parsed_locator.password is not None:
                raise RegistryIntegrityError("evidence locator cannot contain credentials")
            safe_locator = urlunsplit(parsed_locator._replace(fragment=""))
            if not valid_http_uri(safe_locator, max_length=2048):
                raise RegistryIntegrityError("evidence locator is malformed")
            if not _host_in_domains(parsed_locator.hostname, source_domains):
                raise RegistryIntegrityError("evidence locator is outside its registered source domains")
        elif record_type == "story_decision":
            story_ids = set(payload["story_ids"])
            if payload["canonical_story_id"] not in story_ids:
                raise RegistryIntegrityError(
                    "story decision canonical story must be one of the affected stories"
                )
            story_payloads = {
                story_id: require_record("story", story_id, "story decision")
                for story_id in story_ids
            }

            expected_story_revisions = payload["expected_story_revisions"]
            expected_story_ids = [item["story_id"] for item in expected_story_revisions]
            if len(expected_story_ids) != len(set(expected_story_ids)) or set(expected_story_ids) != story_ids:
                raise RegistryIntegrityError(
                    "story decision must capture exactly one expected revision for every affected story"
                )
            for item in expected_story_revisions:
                row = connection.execute(
                    "SELECT revision FROM domain_records WHERE record_type = 'story' AND id = ?",
                    (item["story_id"],),
                ).fetchone()
                if row is None or row["revision"] != item["expected_revision"]:
                    raise RegistryIntegrityError(
                        f"story decision expected revision is stale for story: {item['story_id']}"
                    )

            available_articles = {
                article_id
                for story_payload in story_payloads.values()
                for article_id in story_payload.get("article_ids", [])
            }
            assignments = payload["article_assignments"]
            assigned_articles = [item["article_id"] for item in assignments]
            article_destinations = {
                item["article_id"]: item["assigned_story_id"] for item in assignments
            }
            assigned_stories = {item["assigned_story_id"] for item in assignments}
            if len(assigned_articles) != len(set(assigned_articles)):
                raise RegistryIntegrityError("story decision cannot assign an article more than once")
            for assignment in assignments:
                article_id = assignment["article_id"]
                assigned_story_id = assignment["assigned_story_id"]
                require_record("article", article_id, "story decision")
                if article_id not in available_articles:
                    raise RegistryIntegrityError(
                        "story decision article must belong to at least one affected story"
                    )
                if assigned_story_id not in story_ids:
                    raise RegistryIntegrityError(
                        "story decision article assignment target must be in the decision scope"
                    )
            if payload["operation"] == "merge":
                if assigned_stories != {payload["canonical_story_id"]}:
                    raise RegistryIntegrityError(
                        "merge decision must assign every article to the canonical story"
                    )
                if set(assigned_articles) != available_articles:
                    raise RegistryIntegrityError(
                        "merge decision must account for every article in the affected stories"
                    )
            elif set(assigned_articles) != available_articles:
                raise RegistryIntegrityError(
                    "split decision must account for every article in the affected stories"
                )
            elif len(assigned_stories) < 2:
                raise RegistryIntegrityError(
                    "split decision must allocate articles to at least two resulting stories"
                )
            elif any(
                not any(item["assigned_story_id"] == story_id for item in assignments)
                for story_id in story_ids
            ):
                raise RegistryIntegrityError(
                    "split decision must leave at least one article in every resulting story"
                )

            claim_rows = connection.execute(
                """SELECT id, revision, payload_json FROM domain_records
                WHERE record_type = 'claim'
                  AND json_extract(payload_json, '$.story_id') IN (
                      SELECT value FROM json_each(?)
                  )""",
                (_json_text(sorted(story_ids)),),
            ).fetchall()
            current_claims = {
                row["id"]: (row["revision"], json.loads(row["payload_json"]))
                for row in claim_rows
            }
            claim_assignments = payload["claim_assignments"]
            assigned_claim_ids = [item["claim_id"] for item in claim_assignments]
            if len(assigned_claim_ids) != len(set(assigned_claim_ids)):
                raise RegistryIntegrityError("story decision cannot assign a claim more than once")
            if set(assigned_claim_ids) != set(current_claims):
                raise RegistryIntegrityError(
                    "story decision must account for every claim in the affected stories"
                )
            for item in claim_assignments:
                claim_id = item["claim_id"]
                destination = item["assigned_story_id"]
                current_revision, claim_payload = current_claims[claim_id]
                if destination not in story_ids:
                    raise RegistryIntegrityError(
                        "story decision claim assignment target must be in the decision scope"
                    )
                if current_revision != item["expected_revision"]:
                    raise RegistryIntegrityError(
                        f"story decision expected revision is stale for claim: {claim_id}"
                    )
                provenance_destinations = {
                    article_destinations[entry["article_id"]]
                    for entry in claim_payload.get("provenance", [])
                    if entry.get("article_id") in article_destinations
                }
                if any(
                    entry.get("article_id") is not None
                    and entry["article_id"] not in article_destinations
                    for entry in claim_payload.get("provenance", [])
                ):
                    raise RegistryIntegrityError(
                        f"story decision is missing an article assignment used by claim: {claim_id}"
                    )
                if provenance_destinations and provenance_destinations != {destination}:
                    raise RegistryIntegrityError(
                        f"claim provenance does not fit its assigned resulting story: {claim_id}"
                    )
                if payload["operation"] == "merge" and destination != payload["canonical_story_id"]:
                    raise RegistryIntegrityError(
                        "merge decision must assign every claim to the canonical story"
                    )
        elif record_type == "edition":
            for story_id in payload["story_ids"]:
                require_record("story", story_id, "edition")

    @staticmethod
    def _domain_record_timestamp(record_type: str, payload: dict[str, Any]) -> str:
        field = {
            "article": "discovered_at",
            "story": "created_at",
            "claim": "created_at",
            "evidence": "assessed_at",
            "story_decision": "decided_at",
            "edition": "created_at",
        }[record_type]
        _aware_datetime(payload[field], field)
        return payload[field]

    def add_domain_record(self, record_type: str, payload: dict[str, Any]):
        """Persist a validated domain record."""
        if record_type == "story_decision":
            validate_payload(record_type, payload)
            record = StoryDecisionRecord.from_payload(payload)
            created_at = self._domain_record_timestamp(record_type, payload)
            with self._transaction() as connection:
                self._validate_domain_references(connection, record_type, payload)
                connection.execute(
                    """INSERT INTO story_decisions
                    (id, operation, canonical_story_id, story_ids_json, article_ids_json,
                     decided_by, rationale, decided_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["id"],
                        payload["operation"],
                        payload["canonical_story_id"],
                        _json_text(payload["story_ids"]),
                        _json_text([item["article_id"] for item in payload["article_assignments"]]),
                        payload["decided_by"],
                        payload["rationale"],
                        created_at,
                        record.payload_json,
                    ),
                )
                self._append_audit(
                    connection,
                    event_type="story_decision.created",
                    entity_type="story_decision",
                    entity_id=record.id,
                    payload={"revision": 1, "record": payload},
                )
            return record
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        validate_payload(record_type, payload)
        record = model.from_payload(payload)
        created_at = self._domain_record_timestamp(record_type, payload)
        updated_at = _utc_now()
        with self._transaction() as connection:
            self._validate_domain_references(connection, record_type, payload)
            connection.execute(
                """INSERT INTO domain_records
                (record_type, id, revision, created_at, updated_at, payload_json)
                VALUES (?, ?, 1, ?, ?, ?)""",
                (record_type, record.id, created_at, updated_at, record.payload_json),
            )
            connection.execute(
                """INSERT INTO domain_record_revisions
                (record_type, record_id, revision, created_at, actor, reason, payload_json)
                VALUES (?, ?, 1, ?, ?, ?, ?)""",
                (
                    record_type,
                    record.id,
                    _utc_now(),
                    "system",
                    "record created",
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type=f"{record_type}.created",
                entity_type=record_type,
                entity_id=record.id,
                payload={"revision": 1, "record": payload},
            )
        return record

    def story_reporting_independence(self, story_id: str) -> dict[str, Any]:
        """Summarize publisher count and duplicate-review state without claiming proof of independence."""
        story = self.get_domain_record("story", story_id)
        if story is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        story_payload = story.to_payload()
        article_ids = list(story_payload.get("article_ids", []))
        article_payloads: dict[str, dict[str, Any]] = {}
        for article_id in article_ids:
            article = self.get_domain_record("article", article_id)
            if article is None:
                raise RegistryIntegrityError(
                    f"story references a missing article: {article_id}"
                )
            article_payloads[article_id] = article.to_payload()

        with self._lock:
            rows = self._connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE d.article_id IN (
                    SELECT id FROM domain_records WHERE record_type = 'article'
                )
                ORDER BY d.decided_at, d.decision_id"""
            ).fetchall()

        relevant = set(article_ids)
        latest_review: dict[str, sqlite3.Row] = {}
        for row in rows:
            if row["article_id"] not in relevant:
                continue
            if row["outcome"] not in {
                "possible_duplicate", "confirmed_duplicate", "distinct"
            }:
                continue
            previous = latest_review.get(row["article_id"])
            if previous is None or (
                row["decided_at"], row["decision_id"]
            ) > (
                previous["decided_at"], previous["decision_id"]
            ):
                latest_review[row["article_id"]] = row

        statuses: list[dict[str, Any]] = []
        unresolved: list[dict[str, str]] = []
        confirmed_pairs: list[dict[str, str]] = []
        reviewed_distinct = 0
        no_candidate = 0
        external_confirmed = 0

        parent = {article_id: article_id for article_id in article_ids}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(left: str, right: str) -> None:
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[root_right] = root_left

        for article_id in article_ids:
            payload = article_payloads[article_id]
            decision = latest_review.get(article_id)
            if decision is None:
                status = "no_candidate"
                duplicate_of = None
                no_candidate += 1
            else:
                status = decision["outcome"]
                duplicate_of = decision["duplicate_of_article_id"]
                if status == "possible_duplicate":
                    unresolved.append({
                        "article_id": article_id,
                        "duplicate_of_article_id": duplicate_of or "",
                    })
                elif status == "distinct":
                    reviewed_distinct += 1
                elif status == "confirmed_duplicate":
                    confirmed_pairs.append({
                        "article_id": article_id,
                        "duplicate_of_article_id": duplicate_of or "",
                    })
                    if duplicate_of in parent:
                        union(article_id, duplicate_of)
                    else:
                        external_confirmed += 1
            statuses.append({
                "article_id": article_id,
                "source_id": payload["source_id"],
                "duplicate_status": status,
                "duplicate_of_article_id": duplicate_of,
            })

        grouped: dict[str, list[str]] = {}
        for article_id in article_ids:
            root = find(article_id)
            grouped.setdefault(root, []).append(article_id)
        confirmed_groups = [
            sorted(group) for group in grouped.values() if len(group) > 1
        ]

        return {
            "story_id": story_id,
            "article_count": len(article_ids),
            "publisher_count": len({item["source_id"] for item in statuses}),
            "articles": statuses,
            "unresolved_candidates": unresolved,
            "confirmed_duplicate_pairs": confirmed_pairs,
            "confirmed_duplicate_groups": confirmed_groups,
            "unresolved_candidate_count": len(unresolved),
            "confirmed_duplicate_count": len(confirmed_pairs),
            "external_confirmed_duplicate_count": external_confirmed,
            "reviewed_distinct_count": reviewed_distinct,
            "no_candidate_count": no_candidate,
            "independence_claim": "not_established_by_publisher_count_or_no_candidate",
        }

    def _preview_story_decision_application(
        self, connection: sqlite3.Connection, decision_id: str
    ) -> dict[str, Any]:
        """Build the preview using a caller-owned transaction."""
        decision_row = connection.execute(
            "SELECT payload_json FROM story_decisions WHERE id = ?",
            (decision_id,),
        ).fetchone()
        if decision_row is None:
            raise RegistryIntegrityError(f"unknown story decision: {decision_id}")
        decision = json.loads(decision_row["payload_json"])
        story_ids = sorted(set(decision["story_ids"]))
        application_row = connection.execute(
            """SELECT application_id, applied_at, actor, rationale,
                      decision_fingerprint, impact_fingerprint, result_json
            FROM story_decision_applications WHERE decision_id = ?""",
            (decision_id,),
        ).fetchone()
        application = (
            {
                "application_id": application_row["application_id"],
                "applied_at": application_row["applied_at"],
                "actor": application_row["actor"],
                "rationale": application_row["rationale"],
                "decision_fingerprint": application_row["decision_fingerprint"],
                "impact_fingerprint": application_row["impact_fingerprint"],
                "result": json.loads(application_row["result_json"]),
            }
            if application_row is not None else None
        )

        blockers: list[str] = []
        if application is not None:
            blockers.append("story decision has already been applied")
        try:
            self._validate_domain_references(connection, "story_decision", decision)
        except RegistryIntegrityError as exc:
            blockers.append(str(exc))

        def load_records(record_type: str, ids: list[str]) -> list[dict[str, Any]]:
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"""SELECT id, revision, payload_json FROM domain_records
                WHERE record_type = ? AND id IN ({placeholders}) ORDER BY id""",
                (record_type, *ids),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

        stories = load_records("story", story_ids)
        article_ids = sorted({
            item["article_id"] for item in decision["article_assignments"]
        })
        articles = load_records("article", article_ids)

        claim_rows = connection.execute(
            """SELECT id, revision, payload_json FROM domain_records
            WHERE record_type = 'claim'
              AND json_extract(payload_json, '$.story_id') IN (
                  SELECT value FROM json_each(?)
              )
            ORDER BY id""",
            (_json_text(story_ids),),
        ).fetchall()
        claims = [
            {
                "id": row["id"],
                "revision": row["revision"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in claim_rows
        ]
        claim_ids = [item["id"] for item in claims]
        evidence = load_records("evidence", sorted(
            row["id"]
            for row in connection.execute(
                """SELECT id FROM domain_records
                WHERE record_type = 'evidence'
                  AND json_extract(payload_json, '$.claim_id') IN (
                      SELECT value FROM json_each(?)
                  )""",
                (_json_text(claim_ids),),
            ).fetchall()
        ))

        edition_rows = connection.execute(
            """SELECT DISTINCT d.id, d.revision, d.payload_json
            FROM domain_records d, json_each(d.payload_json, '$.story_ids') story_ref
            WHERE d.record_type = 'edition'
              AND story_ref.value IN (
                  SELECT value FROM json_each(?)
              )
            ORDER BY d.id""",
            (_json_text(story_ids),),
        ).fetchall()
        editions = [
            {
                "id": row["id"],
                "revision": row["revision"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in edition_rows
        ]

        if len(stories) != len(story_ids):
            blockers.append("one or more affected stories are missing")
        if len(articles) != len(article_ids):
            blockers.append("one or more assigned articles are missing")

        story_summaries = [
            {
                "story_id": item["id"],
                "revision": item["revision"],
                "status": item["payload"]["status"],
                "canonical_title": item["payload"]["canonical_title"],
                "article_ids": sorted(item["payload"].get("article_ids", [])),
            }
            for item in stories
        ]
        article_summaries = [
            {
                "article_id": item["id"],
                "source_id": item["payload"]["source_id"],
                "title": item["payload"]["title"],
                "assigned_story_id": next(
                    assignment["assigned_story_id"]
                    for assignment in decision["article_assignments"]
                    if assignment["article_id"] == item["id"]
                ),
            }
            for item in articles
        ]
        claim_destination = {
            item["claim_id"]: item["assigned_story_id"]
            for item in decision["claim_assignments"]
        }
        claim_summaries = [
            {
                "claim_id": item["id"],
                "revision": item["revision"],
                "current_story_id": item["payload"]["story_id"],
                "assigned_story_id": claim_destination.get(item["id"]),
                "assessment": item["payload"]["assessment"],
                "evidence_ids": sorted(item["payload"].get("evidence_ids", [])),
            }
            for item in claims
        ]
        evidence_summaries = [
            {
                "evidence_id": item["id"],
                "claim_id": item["payload"]["claim_id"],
                "source_id": item["payload"]["source_id"],
                "relationship": item["payload"]["relationship"],
            }
            for item in evidence
        ]
        edition_summaries = [
            {
                "edition_id": item["id"],
                "status": item["payload"]["status"],
                "content_hash": item["payload"]["content_hash"],
                "affected_story_ids": sorted(
                    set(item["payload"]["story_ids"]) & set(story_ids)
                ),
            }
            for item in editions
        ]

        fingerprint_payload = {
            "decision": decision,
            "stories": stories,
            "articles": articles,
            "claims": claims,
            "evidence": evidence,
            "editions": editions,
            "application": application,
            "blockers": sorted(set(blockers)),
        }
        return {
            "decision_id": decision_id,
            "operation": decision["operation"],
            "applicable": not blockers,
            "already_applied": application is not None,
            "application": application,
            "blockers": sorted(set(blockers)),
            "impact_fingerprint": sha256_fingerprint(fingerprint_payload),
            "stories": story_summaries,
            "articles": article_summaries,
            "claims": claim_summaries,
            "evidence": evidence_summaries,
            "editions": edition_summaries,
        }


    def preview_story_decision_application(self, decision_id: str) -> dict[str, Any]:
        """Return a deterministic, read-only impact preview for a story decision."""
        with self._transaction("DEFERRED") as connection:
            return self._preview_story_decision_application(connection, decision_id)

    def apply_story_decision(
        self,
        decision_id: str,
        *,
        expected_impact_fingerprint: str,
        actor: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Atomically apply a reviewed story decision using its exact preview fingerprint."""
        if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 200:
            raise ValueError("actor must contain 1 to 200 non-whitespace characters")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale.strip()) > 4000:
            raise ValueError("rationale must contain 1 to 4000 non-whitespace characters")
        if not isinstance(expected_impact_fingerprint, str):
            raise ValueError("expected_impact_fingerprint is required")

        with self._transaction("IMMEDIATE") as connection:
            existing = connection.execute(
                """SELECT application_id, applied_at, actor, rationale, result_json
                FROM story_decision_applications WHERE decision_id = ?""",
                (decision_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "decision_id": decision_id,
                    "application_id": existing["application_id"],
                    "applied_at": existing["applied_at"],
                    "actor": existing["actor"],
                    "rationale": existing["rationale"],
                    "already_applied": True,
                    "result": json.loads(existing["result_json"]),
                }

            preview = self._preview_story_decision_application(connection, decision_id)
            if not preview["applicable"]:
                raise RegistryIntegrityError(
                    "story decision cannot be applied: " + "; ".join(preview["blockers"])
                )
            if preview["impact_fingerprint"] != expected_impact_fingerprint:
                raise RegistryIntegrityError("story decision preview is stale; create a new preview")

            decision_row = connection.execute(
                "SELECT payload_json FROM story_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if decision_row is None:
                raise RegistryIntegrityError(f"unknown story decision: {decision_id}")
            decision = json.loads(decision_row["payload_json"])
            decision_fingerprint = sha256_fingerprint(decision)
            story_ids = sorted(decision["story_ids"])
            article_destinations = {
                item["article_id"]: item["assigned_story_id"]
                for item in decision["article_assignments"]
            }
            claim_destinations = {
                item["claim_id"]: item["assigned_story_id"]
                for item in decision["claim_assignments"]
            }
            now = _utc_now()
            story_changes: list[dict[str, Any]] = []
            claim_changes: list[dict[str, Any]] = []
            changed_story_payloads: dict[str, dict[str, Any]] = {}
            changed_claim_payloads: dict[str, dict[str, Any]] = {}

            for story_id in story_ids:
                row = connection.execute(
                    "SELECT revision, payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                    (story_id,),
                ).fetchone()
                if row is None:
                    raise RegistryIntegrityError(f"affected story disappeared: {story_id}")
                old_revision = row["revision"]
                payload = json.loads(row["payload_json"])
                new_articles = sorted(
                    article_id for article_id, destination in article_destinations.items()
                    if destination == story_id
                )
                if decision["operation"] == "merge" and story_id != decision["canonical_story_id"]:
                    payload["status"] = "archived"
                    payload["article_ids"] = []
                else:
                    payload["article_ids"] = new_articles
                if "revision" in payload:
                    payload["revision"] = old_revision + 1
                if "updated_at" in payload:
                    payload["updated_at"] = now
                validate_payload("story", payload)
                next_revision = old_revision + 1
                encoded = _json_text(payload)
                updated = connection.execute(
                    """UPDATE domain_records SET revision = ?, updated_at = ?, payload_json = ?
                    WHERE record_type = 'story' AND id = ? AND revision = ?""",
                    (next_revision, now, encoded, story_id, old_revision),
                )
                if updated.rowcount != 1:
                    raise RegistryIntegrityError(f"story revision conflict: {story_id}")
                connection.execute(
                    """INSERT INTO domain_record_revisions
                    (record_type, record_id, revision, created_at, actor, reason, payload_json)
                    VALUES ('story', ?, ?, ?, ?, ?, ?)""",
                    (story_id, next_revision, now, actor.strip(), rationale.strip(), encoded),
                )
                changed_story_payloads[story_id] = payload
                story_changes.append({
                    "story_id": story_id,
                    "from_revision": old_revision,
                    "to_revision": next_revision,
                    "status": payload["status"],
                    "article_ids": payload["article_ids"],
                })

            for claim_id in sorted(claim_destinations):
                row = connection.execute(
                    "SELECT revision, payload_json FROM domain_records WHERE record_type = 'claim' AND id = ?",
                    (claim_id,),
                ).fetchone()
                if row is None:
                    raise RegistryIntegrityError(f"assigned claim disappeared: {claim_id}")
                old_revision = row["revision"]
                payload = json.loads(row["payload_json"])
                payload["story_id"] = claim_destinations[claim_id]
                validate_payload("claim", payload)
                next_revision = old_revision + 1
                encoded = _json_text(payload)
                updated = connection.execute(
                    """UPDATE domain_records SET revision = ?, updated_at = ?, payload_json = ?
                    WHERE record_type = 'claim' AND id = ? AND revision = ?""",
                    (next_revision, now, encoded, claim_id, old_revision),
                )
                if updated.rowcount != 1:
                    raise RegistryIntegrityError(f"claim revision conflict: {claim_id}")
                connection.execute(
                    """INSERT INTO domain_record_revisions
                    (record_type, record_id, revision, created_at, actor, reason, payload_json)
                    VALUES ('claim', ?, ?, ?, ?, ?, ?)""",
                    (claim_id, next_revision, now, actor.strip(), rationale.strip(), encoded),
                )
                changed_claim_payloads[claim_id] = payload
                claim_changes.append({
                    "claim_id": claim_id,
                    "from_revision": old_revision,
                    "to_revision": next_revision,
                    "story_id": payload["story_id"],
                })

            # Validate the completed graph only after both sides of every
            # story/article and claim/provenance move have been updated.
            for story_id, payload in changed_story_payloads.items():
                self._validate_domain_references(connection, "story", payload)
            for claim_id, payload in changed_claim_payloads.items():
                self._validate_domain_references(connection, "claim", payload)
                evidence_rows = connection.execute(
                    "SELECT id, payload_json FROM domain_records WHERE record_type = 'evidence' AND json_extract(payload_json, '$.claim_id') = ?",
                    (claim_id,),
                ).fetchall()
                for evidence_row in evidence_rows:
                    self._validate_domain_references(
                        connection, "evidence", json.loads(evidence_row["payload_json"])
                    )

            result = {
                "decision_id": decision_id,
                "operation": decision["operation"],
                "canonical_story_id": decision["canonical_story_id"],
                "stories": story_changes,
                "claims": claim_changes,
                "preserved_evidence_ids": sorted(item["evidence_id"] for item in preview["evidence"]),
                "preserved_editions": [
                    {"edition_id": item["edition_id"], "content_hash": item["content_hash"]}
                    for item in preview["editions"]
                ],
            }
            application_id = f"story-application:{uuid.uuid4().hex}"
            impact_fingerprint = preview["impact_fingerprint"]
            connection.execute(
                """INSERT INTO story_decision_applications
                (application_id, decision_id, applied_at, actor, rationale,
                 decision_fingerprint, impact_fingerprint, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (application_id, decision_id, now, actor.strip(), rationale.strip(),
                 decision_fingerprint, impact_fingerprint, _json_text(result)),
            )
            self._append_audit(
                connection,
                event_type="story_decision.applied",
                entity_type="story_decision",
                entity_id=decision_id,
                payload={
                    "application_id": application_id,
                    "actor": actor.strip(),
                    "rationale": rationale.strip(),
                    "decision_fingerprint": decision_fingerprint,
                    "impact_fingerprint": impact_fingerprint,
                    "result": result,
                },
            )
            return {
                "decision_id": decision_id,
                "application_id": application_id,
                "applied_at": now,
                "actor": actor.strip(),
                "rationale": rationale.strip(),
                "already_applied": False,
                "result": result,
            }


    def add_timeline_event(self, payload: dict[str, Any]) -> TimelineEventRecord:
        """Append a claim-backed event assertion; corrections supersede, never rewrite."""
        validate_payload("timeline-event", payload)
        occurred = payload["occurred"]
        precision = occurred["precision"]
        start = occurred.get("start")
        end = occurred.get("end")
        sort_key = None
        if precision in {"instant", "range"}:
            if not isinstance(start, str):
                raise RegistryIntegrityError("timeline event precision requires a start timestamp")
            parsed_start = _aware_datetime(start, "occurred.start")
            sort_key = parsed_start.isoformat()
            if precision == "range":
                if not isinstance(end, str):
                    raise RegistryIntegrityError("timeline event range requires an end timestamp")
                parsed_end = _aware_datetime(end, "occurred.end")
                if parsed_end < parsed_start:
                    raise RegistryIntegrityError("timeline event end cannot precede its start")
            elif end is not None:
                raise RegistryIntegrityError("instant timeline event cannot include an end")
        elif precision == "date":
            if not isinstance(start, str):
                raise RegistryIntegrityError("date precision requires an ISO date start")
            try:
                from datetime import date
                sort_key = date.fromisoformat(start).isoformat()
            except ValueError as exc:
                raise RegistryIntegrityError("date precision requires an ISO calendar date") from exc
            if end is not None:
                raise RegistryIntegrityError("date precision cannot include an end")
        elif precision == "approximate":
            if not isinstance(start, str) or not isinstance(occurred.get("note"), str) or not occurred["note"].strip():
                raise RegistryIntegrityError("approximate event timing requires a date and an uncertainty note")
            try:
                from datetime import date
                sort_key = date.fromisoformat(start).isoformat()
            except ValueError as exc:
                raise RegistryIntegrityError("approximate event timing requires an ISO calendar date") from exc
            if end is not None:
                raise RegistryIntegrityError("approximate event timing cannot include an end")
        elif precision == "unknown":
            if start is not None or end is not None:
                raise RegistryIntegrityError("unknown event timing cannot include a start or end")
        timezone_name = occurred.get("timezone")
        if timezone_name:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(timezone_name)
            except (KeyError, ValueError) as exc:
                raise RegistryIntegrityError("timeline event timezone must be a valid IANA timezone") from exc

        record = TimelineEventRecord.from_payload(payload)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("timeline event references an unknown story")
            story_payload = json.loads(story_row["payload_json"])
            if story_payload.get("status") == "archived":
                raise RegistryIntegrityError("cannot append a timeline event to an archived story")
            claim_ids = payload["claim_ids"]
            placeholders = ",".join("?" for _ in claim_ids)
            claim_rows = connection.execute(
                f"""SELECT id, payload_json FROM domain_records
                WHERE record_type = 'claim' AND id IN ({placeholders})""",
                tuple(claim_ids),
            ).fetchall()
            if len(claim_rows) != len(claim_ids):
                raise RegistryIntegrityError("timeline event references an unknown claim")
            if any(json.loads(row["payload_json"])["story_id"] != record.story_id for row in claim_rows):
                raise RegistryIntegrityError("timeline event claims must belong to its story")
            supersedes_id = payload.get("supersedes_event_id")
            if supersedes_id:
                prior = connection.execute(
                    "SELECT story_id FROM timeline_events WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior is None or prior["story_id"] != record.story_id:
                    raise RegistryIntegrityError("timeline correction must supersede an event in the same story")
            connection.execute(
                """INSERT INTO timeline_events
                (id, story_id, occurred_sort_key, precision, supersedes_event_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, sort_key, precision, supersedes_id,
                 payload["created_at"], record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="timeline_event.created",
                entity_type="timeline_event",
                entity_id=record.id,
                payload={"record": payload, "supersedes_event_id": supersedes_id},
            )
        return record

    def get_timeline_event(self, event_id: str) -> TimelineEventRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM timeline_events WHERE id = ?", (event_id,)
            ).fetchone()
        return TimelineEventRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def timeline_event_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return all event assertions and correction links, including superseded records."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, occurred_sort_key, precision, supersedes_event_id, created_at, payload_json
                FROM timeline_events WHERE story_id = ?
                ORDER BY CASE WHEN occurred_sort_key IS NULL THEN 1 ELSE 0 END,
                         occurred_sort_key, created_at, id""",
                (story_id,),
            ).fetchall()
        superseded = {row["supersedes_event_id"] for row in rows if row["supersedes_event_id"]}
        return [
            {
                "event_id": row["id"],
                "precision": row["precision"],
                "occurred_sort_key": row["occurred_sort_key"],
                "created_at": row["created_at"],
                "supersedes_event_id": row["supersedes_event_id"],
                "is_superseded": row["id"] in superseded,
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_timeline(self, story_id: str) -> list[dict[str, Any]]:
        """Return the current event assertions in chronological order, retaining uncertainty."""
        return [
            item for item in self.timeline_event_history(story_id)
            if not item["is_superseded"]
        ]

    def story_decision_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return immutable merge/split decisions involving a story, oldest first."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT payload_json FROM story_decisions
                ORDER BY decided_at, id"""
            ).fetchall()
        return [
            payload
            for row in rows
            if story_id in (payload := json.loads(row["payload_json"]))["story_ids"]
        ]

    def add_perspective(self, payload: dict[str, Any]) -> PerspectiveRecord:
        """Append an attributed perspective; corrections supersede, never rewrite."""
        validate_payload("perspective", payload)
        _aware_datetime(payload["created_at"], "created_at")
        review_status = payload.get("review_status", "unreviewed")
        review_fields = ("reviewed_at", "reviewed_by", "review_note")
        if review_status in {"approved", "rejected"}:
            if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in review_fields):
                raise RegistryIntegrityError("reviewed perspective requires reviewer, timestamp, and note")
            _aware_datetime(payload["reviewed_at"], "reviewed_at")
        elif any(field in payload for field in review_fields):
            raise RegistryIntegrityError("unreviewed perspective cannot contain review metadata")
        record = PerspectiveRecord.from_payload(payload)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("perspective references an unknown story")
            story = json.loads(story_row["payload_json"])
            if story.get("status") == "archived":
                raise RegistryIntegrityError("cannot append a perspective to an archived story")
            source_id = payload.get("source_id")
            article_id = payload.get("article_id")
            if source_id:
                source = connection.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
                if source is None:
                    raise RegistryIntegrityError("perspective references an unknown source")
            if article_id:
                article_row = connection.execute(
                    "SELECT payload_json FROM domain_records WHERE record_type = 'article' AND id = ?",
                    (article_id,),
                ).fetchone()
                if article_row is None:
                    raise RegistryIntegrityError("perspective references an unknown article")
                article = json.loads(article_row["payload_json"])
                if article.get("source_id") != source_id or article_id not in story.get("article_ids", []):
                    raise RegistryIntegrityError("perspective article must belong to its source and story")
            if payload["kind"] == "publisher_framing" and (not source_id or not article_id):
                raise RegistryIntegrityError("publisher framing requires its source and article")
            claim_ids = payload["claim_ids"]
            if claim_ids:
                placeholders = ",".join("?" for _ in claim_ids)
                rows = connection.execute(
                    f"SELECT id, payload_json FROM domain_records WHERE record_type = 'claim' AND id IN ({placeholders})",
                    tuple(claim_ids),
                ).fetchall()
                if len(rows) != len(claim_ids) or any(
                    json.loads(row["payload_json"])["story_id"] != record.story_id for row in rows
                ):
                    raise RegistryIntegrityError("perspective claims must belong to its story")
            supersedes_id = payload.get("supersedes_perspective_id")
            if supersedes_id:
                prior = connection.execute(
                    "SELECT story_id, created_at FROM perspectives WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior is None or prior["story_id"] != record.story_id:
                    raise RegistryIntegrityError("perspective correction must supersede an entry in the same story")
                if _aware_datetime(payload["created_at"], "created_at") < _aware_datetime(prior["created_at"], "prior.created_at"):
                    raise RegistryIntegrityError("perspective correction cannot predate the assertion it supersedes")
            connection.execute(
                """INSERT INTO perspectives
                (id, story_id, source_id, article_id, supersedes_perspective_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, source_id, article_id, supersedes_id,
                 payload["created_at"], record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="perspective.created",
                entity_type="perspective",
                entity_id=record.id,
                payload={"record": payload, "supersedes_perspective_id": supersedes_id},
            )
        return record

    def get_perspective(self, perspective_id: str) -> PerspectiveRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM perspectives WHERE id = ?", (perspective_id,)
            ).fetchone()
        return PerspectiveRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def perspective_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return all attributed framings and corrections, including superseded entries."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, source_id, article_id, supersedes_perspective_id, created_at, payload_json
                FROM perspectives WHERE story_id = ? ORDER BY created_at, id""",
                (story_id,),
            ).fetchall()
        superseded = {row["supersedes_perspective_id"] for row in rows if row["supersedes_perspective_id"]}
        return [
            {
                "perspective_id": row["id"],
                "source_id": row["source_id"],
                "article_id": row["article_id"],
                "created_at": row["created_at"],
                "supersedes_perspective_id": row["supersedes_perspective_id"],
                "is_superseded": row["id"] in superseded,
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_perspectives(self, story_id: str) -> list[dict[str, Any]]:
        """Return current attributed perspectives without rewriting prior assertions."""
        return [
            item for item in self.perspective_history(story_id)
            if not item["is_superseded"]
        ]

    def add_claim_relation(self, payload: dict[str, Any]) -> ClaimRelationRecord:
        """Append an explicit relationship between two claims in one story."""
        validate_payload("claim-relation", payload)
        payload = dict(payload)
        _aware_datetime(payload["created_at"], "created_at")
        if payload["claim_id"] == payload["related_claim_id"]:
            raise RegistryIntegrityError("claim relation must connect two different claims")
        if payload["relationship"] == "contradicts" and payload["related_claim_id"] < payload["claim_id"]:
            payload["claim_id"], payload["related_claim_id"] = payload["related_claim_id"], payload["claim_id"]
        review_status = payload["review_status"]
        review_fields = ("reviewed_at", "reviewed_by", "review_note")
        if review_status in {"accepted", "rejected"}:
            if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in review_fields):
                raise RegistryIntegrityError("reviewed claim relation requires reviewer, timestamp, and note")
            _aware_datetime(payload["reviewed_at"], "reviewed_at")
        elif any(field in payload for field in review_fields):
            raise RegistryIntegrityError("unreviewed claim relation cannot contain review metadata")
        record = ClaimRelationRecord.from_payload(payload)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("claim relation references an unknown story")
            story = json.loads(story_row["payload_json"])
            if story.get("status") == "archived":
                raise RegistryIntegrityError("cannot append a claim relation to an archived story")
            ids = (payload["claim_id"], payload["related_claim_id"])
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT id, payload_json FROM domain_records WHERE record_type = 'claim' AND id IN ({placeholders})",
                ids,
            ).fetchall()
            if len(rows) != 2 or any(json.loads(row["payload_json"])["story_id"] != record.story_id for row in rows):
                raise RegistryIntegrityError("claim relation endpoints must belong to its story")
            supersedes_id = payload.get("supersedes_relation_id")
            if supersedes_id:
                prior = connection.execute(
                    "SELECT story_id, created_at FROM claim_relations WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior is None or prior["story_id"] != record.story_id:
                    raise RegistryIntegrityError("claim relation correction must supersede a relation in the same story")
                if _aware_datetime(payload["created_at"], "created_at") < _aware_datetime(prior["created_at"], "prior.created_at"):
                    raise RegistryIntegrityError("claim relation correction cannot predate the relation it supersedes")
            connection.execute(
                """INSERT INTO claim_relations
                (id, story_id, claim_id, related_claim_id, relationship, supersedes_relation_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, record.claim_id, record.related_claim_id,
                 record.relationship, supersedes_id, payload["created_at"], record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="claim_relation.created",
                entity_type="claim_relation",
                entity_id=record.id,
                payload={"record": payload, "supersedes_relation_id": supersedes_id},
            )
        return record

    def get_claim_relation(self, relation_id: str) -> ClaimRelationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM claim_relations WHERE id = ?", (relation_id,)
            ).fetchone()
        return ClaimRelationRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def claim_relation_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return all explicit claim relationships, including superseded assertions."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, claim_id, related_claim_id, relationship,
                          supersedes_relation_id, created_at, payload_json
                FROM claim_relations WHERE story_id = ?
                ORDER BY julianday(created_at), created_at, id""",
                (story_id,),
            ).fetchall()
        superseded = {row["supersedes_relation_id"] for row in rows if row["supersedes_relation_id"]}
        return [
            {
                "relation_id": row["id"],
                "claim_id": row["claim_id"],
                "related_claim_id": row["related_claim_id"],
                "relationship": row["relationship"],
                "created_at": row["created_at"],
                "supersedes_relation_id": row["supersedes_relation_id"],
                "is_superseded": row["id"] in superseded,
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_claim_relations(self, story_id: str) -> list[dict[str, Any]]:
        """Return current reviewed and unreviewed relationships without resolving disputes."""
        return [
            item for item in self.claim_relation_history(story_id)
            if not item["is_superseded"]
        ]

    def get_domain_record(self, record_type: str, record_id: str):
        if record_type == "story_decision":
            with self._lock:
                row = self._connection.execute(
                    "SELECT payload_json FROM story_decisions WHERE id = ?",
                    (record_id,),
                ).fetchone()
            return StoryDecisionRecord.from_payload(json.loads(row["payload_json"])) if row else None
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        with self._lock:
            row = self._connection.execute(
                """SELECT payload_json FROM domain_records
                WHERE record_type = ? AND id = ?""",
                (record_type, record_id),
            ).fetchone()
        return model.from_payload(json.loads(row["payload_json"])) if row else None

    def revise_domain_record(
        self,
        record_type: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ):
        """Append a domain-record revision using optimistic concurrency control."""
        if record_type == "story_decision":
            raise RegistryIntegrityError("story decision records are immutable")
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        if payload.get("id") != record_id:
            raise RegistryIntegrityError("record identity cannot change during revision")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        payload = dict(payload)
        updated_at = _utc_now()
        if record_type == "story":
            if "revision" in payload:
                payload["revision"] = expected_revision + 1
            if "updated_at" in payload:
                payload["updated_at"] = updated_at
        validate_payload(record_type, payload)
        record = model.from_payload(payload)
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT revision, payload_json FROM domain_records
                WHERE record_type = ? AND id = ?""",
                (record_type, record_id),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot revise an unknown domain record")
            if row["revision"] != expected_revision:
                raise RegistryIntegrityError("domain record revision conflict")
            if record_type == "edition":
                raise RegistryIntegrityError("edition snapshots are immutable")
            previous_payload = json.loads(row["payload_json"])
            immutable_time_field = {
                "article": "discovered_at",
                "story": "created_at",
                "claim": "created_at",
                "evidence": "assessed_at",
            }[record_type]
            if payload[immutable_time_field] != previous_payload[immutable_time_field]:
                raise RegistryIntegrityError("record creation/observation time cannot be rewritten")
            immutable_identity_fields = {
                "article": ("source_id", "canonical_uri"),
                "evidence": ("source_id", "claim_id", "article_id", "observation_id", "locator"),
            }.get(record_type, ())
            if any(payload.get(field) != previous_payload.get(field) for field in immutable_identity_fields):
                raise RegistryIntegrityError("domain record identity links cannot be rewritten")
            self._validate_domain_references(connection, record_type, payload)
            next_revision = expected_revision + 1
            updated = connection.execute(
                """UPDATE domain_records SET revision = ?, updated_at = ?, payload_json = ?
                WHERE record_type = ? AND id = ? AND revision = ?""",
                (
                    next_revision,
                    updated_at,
                    record.payload_json,
                    record_type,
                    record_id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise RegistryIntegrityError("domain record revision conflict")
            connection.execute(
                """INSERT INTO domain_record_revisions
                (record_type, record_id, revision, created_at, actor, reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_type,
                    record_id,
                    next_revision,
                    _utc_now(),
                    actor,
                    reason,
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type=f"{record_type}.revised",
                entity_type=record_type,
                entity_id=record_id,
                payload={
                    "revision": next_revision,
                    "actor": actor,
                    "reason": reason,
                    "record": payload,
                },
            )
        return record

    def domain_record_history(self, record_type: str, record_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM domain_record_revisions
                WHERE record_type = ? AND record_id = ? ORDER BY revision""",
                (record_type, record_id),
            ).fetchall()
        return [
            {
                "revision": row["revision"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def schema_version(self) -> int:
        with self._lock:
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])
