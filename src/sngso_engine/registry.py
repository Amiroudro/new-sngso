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
from urllib.parse import urlsplit

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
    StoryRecord,
)
from .permissions import OPERATIONS, PermissionDecision, evaluate_permission as evaluate_permission_record


POLICY_VERSION = "permission-evaluator/1.0.0"
_SCHEMA_VERSION = 4


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
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 4 failed; transaction rolled back") from exc

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
            for article_id in payload.get("article_ids", []):
                require_record("article", article_id, "story")
        elif record_type == "claim":
            require_record("story", payload["story_id"], "claim")
            for provenance in payload["provenance"]:
                source = connection.execute(
                    "SELECT 1 FROM sources WHERE id = ?", (provenance["source_id"],)
                ).fetchone()
                if source is None:
                    raise RegistryIntegrityError("claim provenance references an unknown source")
                article_id = provenance.get("article_id")
                if article_id:
                    article = require_record("article", article_id, "claim provenance")
                    if article["source_id"] != provenance["source_id"]:
                        raise RegistryIntegrityError(
                            "claim provenance article and source do not match"
                        )
        elif record_type == "evidence":
            require_record("claim", payload["claim_id"], "evidence")
            source = connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (payload["source_id"],)
            ).fetchone()
            if source is None:
                raise RegistryIntegrityError("evidence references an unknown source")
            article_id = payload.get("article_id")
            if article_id:
                article = require_record("article", article_id, "evidence")
                if article["source_id"] != payload["source_id"]:
                    raise RegistryIntegrityError(
                        "evidence article and source do not match"
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
            "edition": "created_at",
        }[record_type]
        _aware_datetime(payload[field], field)
        return payload[field]

    def add_domain_record(self, record_type: str, payload: dict[str, Any]):
        """Persist a validated article/story/claim/evidence/edition record."""
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

    def get_domain_record(self, record_type: str, record_id: str):
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
                "evidence": ("source_id", "claim_id"),
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
