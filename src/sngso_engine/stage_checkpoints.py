"""Durable, metadata-only stage checkpoints and conservative crash recovery.

This API is deliberately separate from the legacy pipeline runner. It never
persists stage contexts or outputs; only deterministic fingerprints and bounded
operational metadata are stored.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from dataclasses import replace
from datetime import datetime, timezone
import re
from typing import Any, Callable, Mapping

from .canonical import sha256_fingerprint
from .registry import RegistryIntegrityError


class StageCheckpointError(RuntimeError):
    """A checkpoint cannot be safely created, resumed, or transitioned."""


_ALLOWED_METADATA = {"adapter_version", "provider_state", "failure_code", "reconciliation", "attempt"}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_NAMESPACE = re.compile(r"[a-z][a-z0-9_.-]{0,99}")


def stage_idempotency_key(
    pipeline_run_id: str, stage_ordinal: int, stage_name: str, input_fingerprint: str
) -> str:
    """Return a stable key bound to immutable run, stage, and exact input identity."""
    if not isinstance(pipeline_run_id, str) or not pipeline_run_id.strip():
        raise ValueError("pipeline_run_id is required")
    if isinstance(stage_ordinal, bool) or not isinstance(stage_ordinal, int) or stage_ordinal < 0:
        raise ValueError("stage_ordinal must be a non-negative integer")
    if not isinstance(stage_name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", stage_name):
        raise ValueError("stage_name must be a lowercase bounded identifier")
    if not isinstance(input_fingerprint, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", input_fingerprint):
        raise ValueError("input_fingerprint must be a SHA-256 fingerprint")
    material = {
        "contract": "sngso-stage-idempotency-v1",
        "pipeline_run_id": pipeline_run_id,
        "stage_ordinal": stage_ordinal,
        "stage_name": stage_name,
        "input_fingerprint": input_fingerprint,
    }
    return sha256_fingerprint(material)


def _safe_metadata(value: Mapping[str, Any] | None) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping) or set(value) - _ALLOWED_METADATA:
        raise ValueError("checkpoint metadata contains unsupported fields")
    safe: dict[str, str | int] = {}
    for key, item in value.items():
        if key == "attempt":
            if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 1000:
                raise ValueError("metadata attempt must be an integer from 1 to 1000")
            safe[key] = item
        else:
            if not isinstance(item, str) or not _TOKEN.fullmatch(item):
                raise ValueError(f"metadata {key} must be a bounded opaque token")
            safe[key] = item
    return json.dumps(safe, sort_keys=True, separators=(",", ":"))


def _actor_reason(actor: str, reason: str) -> tuple[str, str]:
    if not isinstance(actor, str) or not _TOKEN.fullmatch(actor):
        raise ValueError("actor must be a bounded identifier")
    if not isinstance(reason, str) or not _TOKEN.fullmatch(reason):
        raise ValueError("reason must be a bounded non-sensitive identifier")
    return actor, reason


@dataclass(frozen=True)
class StageCheckpoint:
    pipeline_run_id: str
    stage_ordinal: int
    stage_name: str
    input_fingerprint: str
    idempotency_key: str
    status: str
    provider_namespace: str | None
    provider_operation_ref: str | None
    output_fingerprint: str | None
    revision: int
    metadata: dict[str, Any]
    created: bool = False


class StageCheckpointStore:
    """Transactional checkpoint access layered on the registry's SQLite connection."""

    def __init__(self, registry: Any) -> None:
        if not hasattr(registry, "_transaction") or not hasattr(registry, "_append_audit"):
            raise TypeError("registry must be a compatible SQLiteRegistry")
        self.registry = registry

    @staticmethod
    def _decode(row: Any) -> StageCheckpoint:
        return StageCheckpoint(
            pipeline_run_id=row["pipeline_run_id"],
            stage_ordinal=row["stage_ordinal"],
            stage_name=row["stage_name"],
            input_fingerprint=row["input_fingerprint"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            provider_namespace=row["provider_namespace"],
            provider_operation_ref=row["provider_operation_ref"],
            output_fingerprint=row["output_fingerprint"],
            revision=row["revision"],
            metadata=json.loads(row["metadata_json"]),
        )

    def get(self, pipeline_run_id: str, stage_ordinal: int) -> StageCheckpoint | None:
        with self.registry._lock:
            row = self.registry._connection.execute(
                """SELECT * FROM pipeline_stage_checkpoints
                   WHERE pipeline_run_id = ? AND stage_ordinal = ?""",
                (pipeline_run_id, stage_ordinal),
            ).fetchone()
        return None if row is None else self._decode(row)

    def history(self, pipeline_run_id: str, stage_ordinal: int) -> list[dict[str, Any]]:
        with self.registry._lock:
            rows = self.registry._connection.execute(
                """SELECT revision, created_at, actor, reason, status,
                          provider_operation_ref, output_fingerprint, metadata_json
                   FROM pipeline_stage_checkpoint_revisions
                   WHERE pipeline_run_id = ? AND stage_ordinal = ? ORDER BY revision""",
                (pipeline_run_id, stage_ordinal),
            ).fetchall()
        return [{
            "revision": row["revision"], "created_at": row["created_at"],
            "actor": row["actor"], "reason": row["reason"], "status": row["status"],
            "provider_operation_ref": row["provider_operation_ref"],
            "output_fingerprint": row["output_fingerprint"],
            "metadata": json.loads(row["metadata_json"]),
        } for row in rows]

    def begin(
        self, pipeline_run_id: str, stage_ordinal: int, stage_name: str,
        input_fingerprint: str, *, provider_namespace: str | None,
        actor: str, reason: str,
    ) -> StageCheckpoint:
        actor, reason = _actor_reason(actor, reason)
        if provider_namespace is not None and not _NAMESPACE.fullmatch(provider_namespace):
            raise ValueError("provider_namespace must be a lowercase bounded identifier")
        key = stage_idempotency_key(pipeline_run_id, stage_ordinal, stage_name, input_fingerprint)
        now = datetime.now(timezone.utc).isoformat()
        with self.registry._transaction() as connection:
            run = connection.execute("SELECT status FROM pipeline_runs WHERE id = ?", (pipeline_run_id,)).fetchone()
            if run is None or run["status"] != "running":
                raise StageCheckpointError("checkpoint requires an existing running pipeline run")
            existing = connection.execute(
                """SELECT * FROM pipeline_stage_checkpoints
                   WHERE pipeline_run_id = ? AND stage_ordinal = ?""",
                (pipeline_run_id, stage_ordinal),
            ).fetchone()
            if existing is not None:
                current = self._decode(existing)
                if (current.stage_name, current.input_fingerprint, current.idempotency_key,
                    current.provider_namespace) != (stage_name, input_fingerprint, key, provider_namespace):
                    raise StageCheckpointError("checkpoint identity conflicts with the requested stage plan")
                return current
            connection.execute(
                """INSERT INTO pipeline_stage_checkpoints
                   (pipeline_run_id, stage_ordinal, stage_name, input_fingerprint, idempotency_key,
                    status, provider_namespace, provider_operation_ref, output_fingerprint,
                    revision, created_at, updated_at, actor, reason, metadata_json)
                   VALUES (?, ?, ?, ?, ?, 'executing', ?, NULL, NULL, 1, ?, ?, ?, ?, '{}')""",
                (pipeline_run_id, stage_ordinal, stage_name, input_fingerprint, key,
                 provider_namespace, now, now, actor, reason),
            )
            self.registry._append_audit(
                connection, event_type="pipeline_stage_checkpoint.created",
                entity_type="pipeline_stage_checkpoint",
                entity_id=f"{pipeline_run_id}:{stage_ordinal}",
                payload={"revision": 1, "status": "executing", "stage_name": stage_name,
                         "input_fingerprint": input_fingerprint, "idempotency_key": key,
                         "provider_namespace": provider_namespace, "actor": actor, "reason": reason},
            )
            row = connection.execute(
                """SELECT * FROM pipeline_stage_checkpoints
                   WHERE pipeline_run_id = ? AND stage_ordinal = ?""",
                (pipeline_run_id, stage_ordinal),
            ).fetchone()
        return replace(self._decode(row), created=True)

    def transition(
        self, checkpoint: StageCheckpoint, status: str, *,
        actor: str, reason: str, provider_operation_ref: str | None = None,
        output_fingerprint: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StageCheckpoint:
        actor, reason = _actor_reason(actor, reason)
        metadata_json = _safe_metadata(metadata)
        if status not in {"succeeded", "failed", "indeterminate", "blocked"}:
            raise ValueError("unsupported checkpoint transition")
        if provider_operation_ref is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", provider_operation_ref):
            raise ValueError("provider operation reference must be a bounded opaque token")
        if status == "succeeded":
            if not isinstance(output_fingerprint, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", output_fingerprint):
                raise ValueError("successful checkpoint requires an output fingerprint")
        elif output_fingerprint is not None:
            raise ValueError("only successful checkpoints may store an output fingerprint")
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        with self.registry._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM pipeline_stage_checkpoints
                   WHERE pipeline_run_id = ? AND stage_ordinal = ?""",
                (checkpoint.pipeline_run_id, checkpoint.stage_ordinal),
            ).fetchone()
            if row is None or row["revision"] != checkpoint.revision or row["status"] != checkpoint.status:
                raise StageCheckpointError("checkpoint revision conflict")
            connection.execute(
                """UPDATE pipeline_stage_checkpoints
                   SET status = ?, provider_operation_ref = COALESCE(?, provider_operation_ref),
                       output_fingerprint = ?, revision = revision + 1, updated_at = ?,
                       actor = ?, reason = ?, metadata_json = ?
                   WHERE pipeline_run_id = ? AND stage_ordinal = ? AND revision = ?""",
                (status, provider_operation_ref, output_fingerprint, now, actor, reason,
                 metadata_json, checkpoint.pipeline_run_id, checkpoint.stage_ordinal, checkpoint.revision),
            )
            self.registry._append_audit(
                connection, event_type="pipeline_stage_checkpoint.transitioned",
                entity_type="pipeline_stage_checkpoint",
                entity_id=f"{checkpoint.pipeline_run_id}:{checkpoint.stage_ordinal}",
                payload={"from": checkpoint.status, "to": status,
                         "revision": checkpoint.revision + 1, "actor": actor, "reason": reason,
                         "provider_operation_ref": provider_operation_ref,
                         "output_fingerprint": output_fingerprint, "metadata": json.loads(metadata_json)},
            )
            updated = connection.execute(
                """SELECT * FROM pipeline_stage_checkpoints
                   WHERE pipeline_run_id = ? AND stage_ordinal = ?""",
                (checkpoint.pipeline_run_id, checkpoint.stage_ordinal),
            ).fetchone()
        return self._decode(updated)


@dataclass(frozen=True)
class Reconciliation:
    """Provider lookup result: completed, not_started, pending, unknown, or denied."""
    status: str
    output: Any = None
    operation_ref: str | None = None
    metadata: Mapping[str, Any] | None = None


def execute_checkpointed_stage(
    registry: Any, pipeline_run_id: str, stage_ordinal: int, stage_name: str,
    context: dict[str, Any], handler: Callable[[dict[str, Any], str], Any], *,
    actor: str, reason: str, permission_check: Callable[[], bool],
    provider_namespace: str | None = None,
    reconcile: Callable[[str, str | None], Reconciliation] | None = None,
    replay_safe: bool = False,
) -> dict[str, Any]:
    """Execute or reconcile one stage without blindly repeating an external effect.

    The handler receives (in-memory context, stable idempotency key). A side-effecting
    handler must have a provider adapter that accepts this key and can reconcile it.
    """
    if not isinstance(context, dict) or not callable(handler) or not callable(permission_check):
        raise TypeError("context, handler, and permission_check are required")
    input_fingerprint = sha256_fingerprint(context)
    store = StageCheckpointStore(registry)
    checkpoint = store.begin(
        pipeline_run_id, stage_ordinal, stage_name, input_fingerprint,
        provider_namespace=provider_namespace, actor=actor, reason=reason,
    )
    if checkpoint.status == "succeeded":
        return {"status": "succeeded", "output_fingerprint": checkpoint.output_fingerprint,
                "reused": True, "idempotency_key": checkpoint.idempotency_key}
    if checkpoint.status != "executing":
        raise StageCheckpointError(f"checkpoint is {checkpoint.status}; explicit operator action is required")

    if not checkpoint.created:
        if replay_safe:
            pass
        elif reconcile is None:
            store.transition(checkpoint, "indeterminate", actor=actor, reason="reconciliation_unavailable",
                             metadata={"reconciliation": "unavailable"})
            raise StageCheckpointError("existing execution has no reconciliation adapter; stopped indeterminate")
        else:
            try:
                resolution = reconcile(checkpoint.idempotency_key, checkpoint.provider_operation_ref)
            except Exception:
                resolution = Reconciliation("unknown")
            if not isinstance(resolution, Reconciliation) or resolution.status not in {
                "completed", "not_started", "pending", "unknown", "denied"
            }:
                resolution = Reconciliation("unknown")
            if resolution.status == "completed":
                fingerprint = sha256_fingerprint(resolution.output)
                store.transition(checkpoint, "succeeded", actor=actor, reason="provider_confirmed_completed",
                                 provider_operation_ref=resolution.operation_ref,
                                 output_fingerprint=fingerprint,
                                 metadata={"reconciliation": "completed"})
                return {"status": "succeeded", "output_fingerprint": fingerprint,
                        "reused": True, "idempotency_key": checkpoint.idempotency_key}
            if resolution.status == "pending":
                raise StageCheckpointError("provider operation remains pending; no replay attempted")
            if resolution.status == "unknown":
                store.transition(checkpoint, "indeterminate", actor=actor, reason="provider_outcome_unknown",
                                 provider_operation_ref=resolution.operation_ref,
                                 metadata={"reconciliation": "unknown"})
                raise StageCheckpointError("provider outcome is unknown; no replay attempted")
            if resolution.status == "denied":
                store.transition(checkpoint, "blocked", actor=actor, reason="provider_reconciliation_denied",
                                 provider_operation_ref=resolution.operation_ref,
                                 metadata={"reconciliation": "denied"})
                raise StageCheckpointError("provider denied reconciliation; stage blocked")
            # not_started is the only provider result that permits a side-effecting retry.

    if not permission_check():
        store.transition(checkpoint, "blocked", actor=actor, reason="permission_check_denied",
                         metadata={"failure_code": "permission_denied"})
        raise StageCheckpointError("permission check denied stage execution")
    try:
        output = handler(context, checkpoint.idempotency_key)
        output_fingerprint = sha256_fingerprint(output)
    except Exception as exc:
        target = "failed" if replay_safe else "indeterminate"
        store.transition(checkpoint, target, actor=actor, reason="stage_handler_failed",
                         metadata={"failure_code": "handler_exception"})
        raise StageCheckpointError("stage handler failed; exception details were not persisted") from exc
    store.transition(checkpoint, "succeeded", actor=actor, reason="stage_handler_succeeded",
                     output_fingerprint=output_fingerprint)
    return {"status": "succeeded", "output_fingerprint": output_fingerprint,
            "reused": False, "idempotency_key": checkpoint.idempotency_key}
