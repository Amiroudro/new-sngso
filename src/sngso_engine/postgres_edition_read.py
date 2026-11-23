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
            "SELECT revision, status, started_at, payload_json FROM pipeline_runs WHERE id = %s FOR SHARE",
            (pipeline_run_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        revision, indexed_status, indexed_started_at, raw = row
        record = self._object(raw, "pipeline run")
        try:
            validate_payload("pipeline-run", record)
        except (ContractValidationError, FileNotFoundError, ValueError) as exc:
            raise PostgresEditionReadAdapterError(f"pipeline run contract failed: {exc}") from exc
        if (
            record.get("id") != pipeline_run_id
            or record.get("status") != indexed_status
            or record.get("started_at") != indexed_started_at
        ):
            raise PostgresEditionReadAdapterError("pipeline run indexed identity does not match its payload")
        self._cursor.execute(
            """SELECT revision, created_at, actor, reason, payload_json
               FROM pipeline_run_revisions
               WHERE pipeline_run_id = %s
               ORDER BY revision
               FOR SHARE""",
            (pipeline_run_id,),
        )
        history_rows = self._cursor.fetchall()
        if (
            len(history_rows) != revision
            or [item[0] for item in history_rows] != list(range(1, revision + 1))
            or history_rows[-1][4] != raw
        ):
            raise PostgresEditionReadAdapterError("pipeline run history is incomplete or does not match its current revision")
        history = [
            {
                "revision": item[0], "created_at": item[1], "actor": item[2],
                "reason": item[3], "record": self._object(item[4], "pipeline run revision"),
            }
            for item in history_rows
        ]
        return {"revision": revision, "record": record, "history": history}

    def get_pipeline_stage_checkpoints(self, pipeline_run_id: str) -> list[dict[str, Any]]:
        """Read and lock every checkpoint plus its append-only revision history."""
        run = self.get_pipeline_run(pipeline_run_id)
        if run is None:
            raise PostgresEditionReadAdapterError("checkpoint lookup references an unknown pipeline run")
        self._cursor.execute(
            """SELECT stage_ordinal, stage_name, input_fingerprint, idempotency_key,
                      status, provider_namespace, provider_operation_ref, output_fingerprint,
                      revision, created_at, updated_at, actor, reason, metadata_json
               FROM pipeline_stage_checkpoints
               WHERE pipeline_run_id = %s
               ORDER BY stage_ordinal
               FOR SHARE""",
            (pipeline_run_id,),
        )
        rows = self._cursor.fetchall()
        checkpoints: list[dict[str, Any]] = []
        for row in rows:
            (
                ordinal, name, input_fingerprint, idempotency_key, status,
                provider_namespace, operation_ref, output_fingerprint, revision,
                created_at, updated_at, actor, reason, metadata_raw,
            ) = row
            try:
                metadata = json.loads(metadata_raw)
            except (TypeError, ValueError) as exc:
                raise PostgresEditionReadAdapterError("pipeline checkpoint metadata is corrupt") from exc
            if not isinstance(metadata, dict):
                raise PostgresEditionReadAdapterError("pipeline checkpoint metadata must be an object")
            self._cursor.execute(
                """SELECT revision, created_at, actor, reason, status,
                          provider_operation_ref, output_fingerprint, metadata_json
                   FROM pipeline_stage_checkpoint_revisions
                   WHERE pipeline_run_id = %s AND stage_ordinal = %s
                   ORDER BY revision
                   FOR SHARE""",
                (pipeline_run_id, ordinal),
            )
            history_rows = self._cursor.fetchall()
            if (
                len(history_rows) != revision
                or [item[0] for item in history_rows] != list(range(1, revision + 1))
            ):
                raise PostgresEditionReadAdapterError("pipeline checkpoint history is incomplete or does not end at its current revision")
            history = []
            for item in history_rows:
                try:
                    item_metadata = json.loads(item[7])
                except (TypeError, ValueError) as exc:
                    raise PostgresEditionReadAdapterError("pipeline checkpoint revision metadata is corrupt") from exc
                history.append({
                    "revision": item[0], "created_at": item[1], "actor": item[2],
                    "reason": item[3], "status": item[4],
                    "provider_operation_ref": item[5], "output_fingerprint": item[6],
                    "metadata": item_metadata,
                })
            checkpoints.append({
                "pipeline_run_id": pipeline_run_id, "stage_ordinal": ordinal,
                "stage_name": name, "input_fingerprint": input_fingerprint,
                "idempotency_key": idempotency_key, "status": status,
                "provider_namespace": provider_namespace,
                "provider_operation_ref": operation_ref,
                "output_fingerprint": output_fingerprint, "revision": revision,
                "created_at": created_at, "updated_at": updated_at,
                "actor": actor, "reason": reason, "metadata": metadata,
                "history": history,
            })
        return checkpoints

    def get_pipeline_worker_lease(self, pipeline_run_id: str) -> dict[str, Any] | None:
        """Read the current fenced lease and verify its immutable revision head."""
        self._cursor.execute(
            """SELECT worker_id, token_hash, fencing_token, status, acquired_at,
                      heartbeat_at, expires_at, cancel_requested_at,
                      cancel_requested_by, cancel_reason_code, revision, updated_at,
                      actor, reason
               FROM pipeline_worker_leases
               WHERE pipeline_run_id = %s
               FOR SHARE""",
            (pipeline_run_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        self._cursor.execute(
            """SELECT revision, created_at, worker_id, token_hash, fencing_token,
                      status, acquired_at, heartbeat_at, expires_at,
                      cancel_requested_at, cancel_requested_by, cancel_reason_code,
                      actor, reason
               FROM pipeline_worker_lease_revisions
               WHERE pipeline_run_id = %s
               ORDER BY revision
               FOR SHARE""",
            (pipeline_run_id,),
        )
        history = self._cursor.fetchall()
        if (
            len(history) != row[10]
            or [item[0] for item in history] != list(range(1, row[10] + 1))
        ):
            raise PostgresEditionReadAdapterError("pipeline worker lease history is incomplete or does not end at its current revision")
        return {
            "pipeline_run_id": pipeline_run_id, "worker_id": row[0],
            "token_hash": row[1], "fencing_token": row[2], "status": row[3],
            "acquired_at": row[4], "heartbeat_at": row[5], "expires_at": row[6],
            "cancel_requested_at": row[7], "cancel_requested_by": row[8],
            "cancel_reason_code": row[9], "revision": row[10],
            "updated_at": row[11], "actor": row[12], "reason": row[13],
            "history": [
                {
                    "revision": item[0], "created_at": item[1], "worker_id": item[2],
                    "token_hash": item[3], "fencing_token": item[4], "status": item[5],
                    "acquired_at": item[6], "heartbeat_at": item[7], "expires_at": item[8],
                    "cancel_requested_at": item[9], "cancel_requested_by": item[10],
                    "cancel_reason_code": item[11], "actor": item[12], "reason": item[13],
                }
                for item in history
            ],
        }

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
        if payload.get("id") != record_id or (
            "revision" in payload and payload.get("revision") != revision
        ):
            raise PostgresEditionReadAdapterError("domain record identity/revision does not match its key")
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
        if (
            len(history) != current_row[0]
            or [item["revision"] for item in history] != list(range(1, current_row[0] + 1))
            or any(
                item["record"].get("id") != record_id
                or ("revision" in item["record"] and item["record"].get("revision") != item["revision"])
                for item in history
            )
        ):
            raise PostgresEditionReadAdapterError("domain history does not match the current record identity/revision")
        return history

    def get_learning_object(self, learning_object_id: str) -> LearningObjectRecord | None:
        self._cursor.execute(
            """SELECT revision, story_id, kind, language, variant_group_id, status,
                      created_at, payload_json
               FROM learning_objects WHERE id = %s FOR SHARE""",
            (learning_object_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        revision, story_id, kind, language, variant_group_id, status, created_at, raw = row
        payload = self._object(raw, "learning object")
        if (
            payload.get("id") != learning_object_id
            or payload.get("story_id") != story_id
            or payload.get("kind") != kind
            or payload.get("language") != language
            or payload.get("variant_group_id") != variant_group_id
            or payload.get("status") != status
            or payload.get("created_at") != created_at
            or ("revision" in payload and payload.get("revision") != revision)
        ):
            raise PostgresEditionReadAdapterError("learning-object indexed identity/revision does not match its payload")
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
        if (
            len(history) != current.revision
            or [item["revision"] for item in history] != list(range(1, current.revision + 1))
            or any(
                item["record"].get("id") != learning_object_id
                or item["record"].get("story_id") != current.story_id
                or item["record"].get("kind") != current.kind
                or item["record"].get("language") != current.language
                for item in history
            )
        ):
            raise PostgresEditionReadAdapterError("learning-object history does not match current identity/revision")
        return history

    def timeline_event_history(self, story_id: str) -> list[dict[str, Any]]:
        """Read immutable timeline assertions and correction links for one story."""
        if self.get_domain_record("story", story_id) is None:
            raise PostgresEditionReadAdapterError(f"unknown story: {story_id}")
        self._cursor.execute(
            """SELECT id, occurred_sort_key, precision, supersedes_event_id,
                      created_at, payload_json
               FROM timeline_events WHERE story_id = %s
               ORDER BY (occurred_sort_key IS NULL), occurred_sort_key, created_at, id
               FOR SHARE""",
            (story_id,),
        )
        rows = self._cursor.fetchall()
        superseded = {row[3] for row in rows if row[3]}
        return [
            {
                "event_id": row[0], "precision": row[2],
                "occurred_sort_key": row[1], "created_at": row[4],
                "supersedes_event_id": row[3], "is_superseded": row[0] in superseded,
                "record": self._object(row[5], "timeline event"),
            }
            for row in rows
        ]

    def perspective_history(self, story_id: str) -> list[dict[str, Any]]:
        """Read all attributed perspectives, retaining superseded history."""
        if self.get_domain_record("story", story_id) is None:
            raise PostgresEditionReadAdapterError(f"unknown story: {story_id}")
        self._cursor.execute(
            """SELECT id, source_id, article_id, supersedes_perspective_id,
                      created_at, payload_json
               FROM perspectives WHERE story_id = %s
               ORDER BY created_at, id FOR SHARE""",
            (story_id,),
        )
        rows = self._cursor.fetchall()
        superseded = {row[3] for row in rows if row[3]}
        return [
            {
                "perspective_id": row[0], "source_id": row[1], "article_id": row[2],
                "created_at": row[4], "supersedes_perspective_id": row[3],
                "is_superseded": row[0] in superseded,
                "record": self._object(row[5], "perspective"),
            }
            for row in rows
        ]

    def claim_relation_history(self, story_id: str) -> list[dict[str, Any]]:
        """Read all explicit claim relationships, including superseded assertions."""
        if self.get_domain_record("story", story_id) is None:
            raise PostgresEditionReadAdapterError(f"unknown story: {story_id}")
        self._cursor.execute(
            """SELECT id, claim_id, related_claim_id, relationship,
                      supersedes_relation_id, created_at, payload_json
               FROM claim_relations WHERE story_id = %s
               ORDER BY created_at, id FOR SHARE""",
            (story_id,),
        )
        rows = self._cursor.fetchall()
        superseded = {row[4] for row in rows if row[4]}
        return [
            {
                "relation_id": row[0], "claim_id": row[1],
                "related_claim_id": row[2], "relationship": row[3],
                "created_at": row[5], "supersedes_relation_id": row[4],
                "is_superseded": row[0] in superseded,
                "record": self._object(row[6], "claim relation"),
            }
            for row in rows
        ]

    def story_decision_history(self, story_id: str) -> list[dict[str, Any]]:
        """Read immutable merge/split decisions involving the requested story."""
        if self.get_domain_record("story", story_id) is None:
            raise PostgresEditionReadAdapterError(f"unknown story: {story_id}")
        self._cursor.execute(
            "SELECT payload_json FROM story_decisions ORDER BY decided_at, id FOR SHARE"
        )
        decisions = []
        for (raw,) in self._cursor.fetchall():
            payload = self._object(raw, "story decision")
            if story_id in payload.get("story_ids", []):
                decisions.append(payload)
        return decisions

    def get_source(self, source_id: str) -> SourceRecord | None:
        self._cursor.execute(
            "SELECT id, publisher_name, status, payload_json FROM sources WHERE id = %s FOR SHARE",
            (source_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        payload = self._object(row[3], "source")
        if (payload.get("id"), payload.get("publisher_name"), payload.get("status")) != row[:3]:
            raise PostgresEditionReadAdapterError("source indexed fields do not match payload")
        return SourceRecord.from_payload(payload)

    def get_endpoint(self, endpoint_id: str) -> SourceEndpointRecord | None:
        self._cursor.execute(
            "SELECT id, source_id, endpoint_uri, status, payload_json FROM source_endpoints WHERE id = %s FOR SHARE",
            (endpoint_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        payload = self._object(row[4], "source endpoint")
        if (payload.get("id"), payload.get("source_id"), payload.get("endpoint_uri"), payload.get("status")) != row[:4]:
            raise PostgresEditionReadAdapterError("source endpoint indexed fields do not match payload")
        return SourceEndpointRecord.from_payload(payload)

    def get_permission_evidence(self, evidence_id: str) -> PermissionEvidenceRecord | None:
        self._cursor.execute(
            "SELECT id, source_id, review_status, content_fingerprint, payload_json FROM permission_evidence WHERE id = %s FOR SHARE",
            (evidence_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        payload = self._object(row[4], "permission evidence")
        if (payload.get("id"), payload.get("source_id"), payload.get("review_status"), payload.get("content_fingerprint")) != row[:4]:
            raise PostgresEditionReadAdapterError("permission evidence indexed fields do not match payload")
        return PermissionEvidenceRecord.from_payload(payload)

    def get_permission_grant(self, grant_id: str) -> PermissionGrantRecord | None:
        self._cursor.execute(
            "SELECT id, source_id, status, effective_from, effective_until, payload_json FROM permission_grants WHERE id = %s FOR SHARE",
            (grant_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        payload = self._object(row[5], "permission grant")
        if (payload.get("id"), payload.get("source_id"), payload.get("status"), payload.get("effective_from"), payload.get("effective_until")) != row[:5]:
            raise PostgresEditionReadAdapterError("permission grant indexed fields do not match payload")
        return PermissionGrantRecord.from_payload(payload)

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
