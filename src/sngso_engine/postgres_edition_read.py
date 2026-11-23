"""Cursor-bound read adapter for PostgreSQL edition verification.

All database reads stay on the caller's staging transaction cursor. Ordinary
record, history, pipeline and rights lookups lock the rows they inspect.
Story snapshot reconstruction and the detailed learning-object quality report
are explicit injected ports because their SQLite implementations aggregate
many editorial subsystems and must be ported with exact fingerprint parity.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .contracts import ContractValidationError, validate_payload
from .models import (
    ArticleRecord,
    ClaimRecord,
    EditionRecord,
    EvidenceRecord,
    LearningObjectRecord,
    PermissionEvidenceRecord,
    PermissionGrantRecord,
    SourceEndpointRecord,
    SourceRecord,
    StoryRecord,
)
from .postgres_permission_read import evaluate_postgres_grant_for_staging


class PostgresEditionReadAdapterError(RuntimeError):
    """Raised when a PostgreSQL edition verification read cannot be trusted."""


class PostgresEditionReadAdapterIncomplete(PostgresEditionReadAdapterError):
    """Raised when an exact-parity editorial read port has not been supplied."""


class PostgresEditionRegistryReadView:
    """Read-only registry facade over one active PostgreSQL transaction cursor.

    The optional snapshot and quality loaders are trusted application code.
    They must use this same cursor, lock every row they inspect FOR SHARE (or
    stronger), and reproduce the SQLite snapshot/quality semantics exactly.
    """

    _DOMAIN_MODELS = {
        "article": ArticleRecord,
        "story": StoryRecord,
        "claim": ClaimRecord,
        "evidence": EvidenceRecord,
        "edition": EditionRecord,
    }

    def __init__(
        self,
        cursor: Any,
        *,
        story_snapshot_loader: Callable[[Any, str], dict[str, Any]] | None = None,
        learning_object_quality_loader: Callable[[Any, str], dict[str, Any]] | None = None,
    ) -> None:
        self._cursor = cursor
        self._story_snapshot_loader = story_snapshot_loader
        self._learning_object_quality_loader = learning_object_quality_loader

    @staticmethod
    def _object(raw: str, label: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PostgresEditionReadAdapterError(f"{label} is corrupt JSON") from exc
        if not isinstance(value, dict):
            raise PostgresEditionReadAdapterError(f"{label} must be a JSON object")
        return value

    def get_pipeline_run(self, pipeline_run_id: str) -> dict[str, Any] | None:
        self._cursor.execute(
            "SELECT revision, payload_json FROM pipeline_runs WHERE id = %s FOR SHARE",
            (pipeline_run_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        revision, raw = row
        record = self._object(raw, "pipeline run")
        try:
            validate_payload("pipeline-run", record)
        except (ContractValidationError, FileNotFoundError, ValueError) as exc:
            raise PostgresEditionReadAdapterError(f"pipeline run contract failed: {exc}") from exc
        if record.get("id") != pipeline_run_id:
            raise PostgresEditionReadAdapterError("pipeline run identity does not match its key")
        return {"revision": revision, "record": record}

    def get_domain_record(self, record_type: str, record_id: str) -> Any | None:
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        self._cursor.execute(
            """SELECT revision, payload_json FROM domain_records
               WHERE record_type = %s AND id = %s
               FOR SHARE""",
            (record_type, record_id),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        revision, raw = row
        payload = self._object(raw, f"{record_type} record")
        if payload.get("id") != record_id:
            raise PostgresEditionReadAdapterError("domain record identity does not match its key")
        return model.from_payload(payload)

    def domain_record_history(self, record_type: str, record_id: str) -> list[dict[str, Any]]:
        self._cursor.execute(
            """SELECT revision, created_at, actor, reason, payload_json
               FROM domain_record_revisions
               WHERE record_type = %s AND record_id = %s
               ORDER BY revision
               FOR SHARE""",
            (record_type, record_id),
        )
        rows = self._cursor.fetchall()
        history = [
            {
                "revision": revision,
                "created_at": created_at,
                "actor": actor,
                "reason": reason,
                "record": self._object(raw, "domain record revision"),
            }
            for revision, created_at, actor, reason, raw in rows
        ]
        current = self.get_domain_record(record_type, record_id)
        if current is None:
            if history:
                raise PostgresEditionReadAdapterError("domain history exists without a current record")
            return []
        self._cursor.execute(
            """SELECT revision FROM domain_records
               WHERE record_type = %s AND id = %s""",
            (record_type, record_id),
        )
        current_row = self._cursor.fetchone()
        if not history or history[-1]["revision"] != current_row[0]:
            raise PostgresEditionReadAdapterError("domain history does not end at the current record revision")
        return history

    def get_learning_object(self, learning_object_id: str) -> LearningObjectRecord | None:
        self._cursor.execute(
            """SELECT revision, payload_json FROM learning_objects
               WHERE id = %s FOR SHARE""",
            (learning_object_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        revision, raw = row
        payload = self._object(raw, "learning object")
        if payload.get("id") != learning_object_id:
            raise PostgresEditionReadAdapterError("learning object identity does not match its key")
        return LearningObjectRecord.from_payload(payload, revision=revision)

    def learning_object_history(self, learning_object_id: str) -> list[dict[str, Any]]:
        if self.get_learning_object(learning_object_id) is None:
            raise PostgresEditionReadAdapterError(f"unknown learning object: {learning_object_id}")
        self._cursor.execute(
            """SELECT revision, created_at, actor, reason, payload_json
               FROM learning_object_revisions
               WHERE learning_object_id = %s
               ORDER BY revision
               FOR SHARE""",
            (learning_object_id,),
        )
        rows = self._cursor.fetchall()
        history = [
            {
                "revision": revision,
                "created_at": created_at,
                "actor": actor,
                "reason": reason,
                "record": self._object(raw, "learning object revision"),
            }
            for revision, created_at, actor, reason, raw in rows
        ]
        current = self.get_learning_object(learning_object_id)
        if not history or history[-1]["revision"] != current.revision:
            raise PostgresEditionReadAdapterError("learning-object history does not end at the current revision")
        return history

    def get_source(self, source_id: str) -> SourceRecord | None:
        self._cursor.execute(
            "SELECT payload_json FROM sources WHERE id = %s FOR SHARE",
            (source_id,),
        )
        row = self._cursor.fetchone()
        return SourceRecord.from_payload(self._object(row[0], "source")) if row else None

    def get_endpoint(self, endpoint_id: str) -> SourceEndpointRecord | None:
        self._cursor.execute(
            "SELECT payload_json FROM source_endpoints WHERE id = %s FOR SHARE",
            (endpoint_id,),
        )
        row = self._cursor.fetchone()
        return SourceEndpointRecord.from_payload(self._object(row[0], "source endpoint")) if row else None

    def get_permission_evidence(self, evidence_id: str) -> PermissionEvidenceRecord | None:
        self._cursor.execute(
            "SELECT payload_json FROM permission_evidence WHERE id = %s FOR SHARE",
            (evidence_id,),
        )
        row = self._cursor.fetchone()
        return PermissionEvidenceRecord.from_payload(
            self._object(row[0], "permission evidence")
        ) if row else None

    def get_permission_grant(self, grant_id: str) -> PermissionGrantRecord | None:
        self._cursor.execute(
            "SELECT payload_json FROM permission_grants WHERE id = %s FOR SHARE",
            (grant_id,),
        )
        row = self._cursor.fetchone()
        return PermissionGrantRecord.from_payload(
            self._object(row[0], "permission grant")
        ) if row else None

    def evaluate_grant(
        self,
        grant_id: str,
        operation: str,
        *,
        endpoint_id: str,
        now: Any = None,
    ):
        return evaluate_postgres_grant_for_staging(
            self._cursor, grant_id, operation, endpoint_id=endpoint_id, now=now
        )

    def get_benchmark_principal(self, principal_id: str) -> dict[str, Any] | None:
        self._cursor.execute(
            """SELECT principal_id, display_name, roles_json, active,
                      languages_json, principal_kind, created_at, created_by, reason
               FROM benchmark_principals
               WHERE principal_id = %s
               FOR SHARE""",
            (principal_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        (
            principal_id, display_name, roles_raw, active, languages_raw,
            principal_kind, created_at, created_by, reason,
        ) = row
        try:
            roles = json.loads(roles_raw)
            languages = json.loads(languages_raw)
        except (TypeError, ValueError) as exc:
            raise PostgresEditionReadAdapterError("benchmark principal role data is corrupt") from exc
        if not isinstance(roles, list) or not isinstance(languages, list):
            raise PostgresEditionReadAdapterError("benchmark principal role data has the wrong shape")
        return {
            "principal_id": principal_id,
            "display_name": display_name,
            "roles": roles,
            "active": bool(active),
            "languages": languages,
            "principal_kind": principal_kind,
            "created_at": created_at,
            "created_by": created_by,
            "reason": reason,
        }

    def story_editorial_snapshot(self, story_id: str) -> dict[str, Any]:
        if self._story_snapshot_loader is None:
            raise PostgresEditionReadAdapterIncomplete(
                "exact PostgreSQL story snapshot/fingerprint parity is not implemented"
            )
        snapshot = self._story_snapshot_loader(self._cursor, story_id)
        if not isinstance(snapshot, dict) or snapshot.get("story_id") != story_id:
            raise PostgresEditionReadAdapterError("story snapshot loader returned an invalid snapshot")
        return snapshot

    def learning_object_quality_report(self, learning_object_id: str) -> dict[str, Any]:
        if self._learning_object_quality_loader is None:
            raise PostgresEditionReadAdapterIncomplete(
                "PostgreSQL learning-object quality gates are not implemented"
            )
        report = self._learning_object_quality_loader(self._cursor, learning_object_id)
        if not isinstance(report, dict) or not isinstance(
            report.get("meets_registry_quality_gates"), bool
        ):
            raise PostgresEditionReadAdapterError("learning-object quality loader returned an invalid report")
        return report
