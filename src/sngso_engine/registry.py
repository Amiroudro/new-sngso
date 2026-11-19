"""SQLite-backed source registry with cross-record checks and an audit chain."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from .canonical import canonical_json_bytes, sha256_fingerprint
from .contracts import validate_payload
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
_SCHEMA_VERSION = 1


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
                self._connection.commit()

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
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 1 failed; transaction rolled back") from exc

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

    def verify_audit_chain(self) -> bool:
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
                "article": ("source_id",),
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
