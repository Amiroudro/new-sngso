"""Durable, metadata-only stage checkpoints and conservative crash recovery.

This API is deliberately separate from the legacy pipeline runner. It never
persists stage contexts or outputs; only deterministic fingerprints and bounded
operational metadata are stored.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from dataclasses import replace
from datetime import datetime, timezone
import re
from typing import Any, Callable, Mapping

from .canonical import sha256_fingerprint


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
            predecessors = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded
                   FROM pipeline_stage_checkpoints
                   WHERE pipeline_run_id = ? AND stage_ordinal < ?""",
                (pipeline_run_id, stage_ordinal),
            ).fetchone()
            if predecessors["total"] != stage_ordinal or (predecessors["succeeded"] or 0) != stage_ordinal:
                raise StageCheckpointError("all preceding stage checkpoints must exist and be durably succeeded")
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
        if status not in {"executing", "succeeded", "failed", "indeterminate", "blocked"}:
            raise ValueError("unsupported checkpoint transition")
        if provider_operation_ref is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", provider_operation_ref):
            raise ValueError("provider operation reference must be a bounded opaque token")
        if status == "succeeded":
            if not isinstance(output_fingerprint, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", output_fingerprint):
                raise ValueError("successful checkpoint requires an output fingerprint")
        elif output_fingerprint is not None:
            raise ValueError("only successful checkpoints may store an output fingerprint")
        now = datetime.now(timezone.utc).isoformat()
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
    reconstruct: Callable[[StageCheckpoint], Any] | None = None,
    lease_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute or reconcile one stage without blindly repeating an external effect.

    The handler receives (in-memory context, stable idempotency key). A side-effecting
    handler must have a provider adapter that accepts this key and can reconcile it.
    """
    if not isinstance(context, dict) or not callable(handler) or not callable(permission_check):
        raise TypeError("context, handler, and permission_check are required")
    if not isinstance(replay_safe, bool):
        raise TypeError("replay_safe must be an explicit boolean")
    if reconcile is not None and not callable(reconcile):
        raise TypeError("reconcile must be callable when supplied")
    if reconstruct is not None and not callable(reconstruct):
        raise TypeError("reconstruct must be callable when supplied")
    if lease_guard is not None and not callable(lease_guard):
        raise TypeError("lease_guard must be callable when supplied")
    input_fingerprint = sha256_fingerprint(context)
    store = StageCheckpointStore(registry)
    checkpoint = store.begin(
        pipeline_run_id, stage_ordinal, stage_name, input_fingerprint,
        provider_namespace=provider_namespace, actor=actor, reason=reason,
    )
    if checkpoint.status == "succeeded":
        if reconstruct is None:
            raise StageCheckpointError("successful checkpoint requires a result reconstruction callback")
        try:
            reconstructed = reconstruct(checkpoint)
            reconstructed_fingerprint = sha256_fingerprint(reconstructed)
        except Exception as exc:
            raise StageCheckpointError("stage result reconstruction failed") from exc
        if reconstructed_fingerprint != checkpoint.output_fingerprint:
            raise StageCheckpointError("reconstructed stage result fingerprint does not match checkpoint")
        return {"status": "succeeded", "output_fingerprint": checkpoint.output_fingerprint,
                "output": reconstructed, "reused": True, "idempotency_key": checkpoint.idempotency_key}
    if checkpoint.status not in {"executing", "indeterminate"}:
        raise StageCheckpointError(f"checkpoint is {checkpoint.status}; explicit operator action is required")

    if not checkpoint.created:
        if replay_safe:
            pass
        elif reconcile is None:
            if checkpoint.status == "executing":
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
                        "output": resolution.output, "reused": True,
                        "idempotency_key": checkpoint.idempotency_key}
            if resolution.status == "pending":
                raise StageCheckpointError("provider operation remains pending; no replay attempted")
            if resolution.status == "unknown":
                if checkpoint.status == "executing":
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
            if checkpoint.status == "indeterminate":
                checkpoint = store.transition(
                    checkpoint, "executing", actor=actor, reason="provider_confirmed_not_started",
                    provider_operation_ref=resolution.operation_ref,
                    metadata={"reconciliation": "not_started"},
                )

    try:
        permitted = permission_check() is True
    except Exception:
        permitted = False
    if not permitted:
        store.transition(checkpoint, "blocked", actor=actor, reason="permission_check_denied",
                         metadata={"failure_code": "permission_denied"})
        raise StageCheckpointError("permission check denied stage execution")
    if lease_guard is not None:
        lease_guard()
    try:
        output = handler(context, checkpoint.idempotency_key)
        output_fingerprint = sha256_fingerprint(output)
    except Exception as exc:
        # A worker that lost its lease must not mutate the checkpoint after
        # ownership has moved. Leave the executing checkpoint for reconciliation.
        if lease_guard is not None:
            lease_guard()
        target = "failed" if replay_safe else "indeterminate"
        store.transition(checkpoint, target, actor=actor, reason="stage_handler_failed",
                         metadata={"failure_code": "handler_exception"})
        raise StageCheckpointError("stage handler failed; exception details were not persisted") from exc
    if lease_guard is not None:
        lease_guard()
    store.transition(checkpoint, "succeeded", actor=actor, reason="stage_handler_succeeded",
                     output_fingerprint=output_fingerprint)
    return {"status": "succeeded", "output_fingerprint": output_fingerprint,
            "output": output, "reused": False, "idempotency_key": checkpoint.idempotency_key}


@dataclass(frozen=True)
class CheckpointedStage:
    """A stage definition for the durable checkpoint-aware pipeline runner."""

    name: str
    handler: Callable[[dict[str, Any], str], Any]
    permission_check: Callable[[], bool]
    provider_namespace: str | None = None
    reconcile: Callable[[str, str | None], Reconciliation] | None = None
    replay_safe: bool = False
    reconstruct: Callable[[StageCheckpoint], Any] | None = None


def execute_checkpointed_pipeline(
    registry: Any,
    pipeline_run_id: str,
    *,
    input_context: dict[str, Any],
    stages: list[CheckpointedStage] | tuple[CheckpointedStage, ...],
    actor: str,
    reason: str,
    now: datetime | None = None,
    lease_store: Any | None = None,
    lease: Any | None = None,
) -> dict[str, Any]:
    """Run ordered stages from durable checkpoints and complete the pipeline run.

    The run must already be in running state. An indeterminate or pending
    external operation leaves the run running for explicit reconciliation; it
    never marks the run successful or advances to a later stage.
    """
    actor, reason = _actor_reason(actor, reason)
    if (lease_store is None) != (lease is None):
        raise ValueError("lease_store and lease must be supplied together")
    if lease_store is not None and (
        not callable(getattr(lease_store, "assert_current", None))
        or not callable(getattr(lease_store, "cancellation_requested", None))
        or not callable(getattr(lease_store, "acknowledge_cancellation", None))
    ):
        raise TypeError("lease_store does not implement the worker lease contract")
    if not isinstance(input_context, dict):
        raise TypeError("input_context must be a JSON object")
    if not isinstance(stages, (list, tuple)) or not stages:
        raise StageCheckpointError("at least one checkpointed stage is required")
    stage_list = tuple(stages)
    names: set[str] = set()
    for stage in stage_list:
        if not isinstance(stage, CheckpointedStage):
            raise TypeError("stages must be CheckpointedStage instances")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", stage.name) or stage.name in names:
            raise StageCheckpointError("stage names must be unique lowercase bounded identifiers")
        if not callable(stage.handler) or not callable(stage.permission_check):
            raise TypeError("every stage requires a handler and permission check")
        if not isinstance(stage.replay_safe, bool):
            raise TypeError("replay_safe must be an explicit boolean")
        if stage.reconcile is not None and not callable(stage.reconcile):
            raise TypeError("reconcile must be callable when supplied")
        if stage.reconstruct is not None and not callable(stage.reconstruct):
            raise TypeError("reconstruct must be callable when supplied")
        names.add(stage.name)

    finished_at = now or datetime.now(timezone.utc)
    if not isinstance(finished_at, datetime) or finished_at.tzinfo is None or finished_at.utcoffset() is None:
        raise ValueError("completion time must be timezone-aware")
    finished_at = finished_at.astimezone(timezone.utc)

    current = registry.get_pipeline_run(pipeline_run_id)
    if current is None or current["record"]["status"] != "running":
        raise StageCheckpointError("checkpointed pipeline requires an existing running pipeline run")
    if sha256_fingerprint(input_context) not in current["record"]["input_hashes"]:
        raise StageCheckpointError("input context fingerprint is not bound to the running pipeline")

    outputs: dict[str, Any] = {}
    stage_results: list[dict[str, Any]] = []
    checkpoint_store = StageCheckpointStore(registry)
    for ordinal, stage in enumerate(stage_list):
        if lease_store is not None:
            lease_store.assert_current(lease)
            if lease_store.cancellation_requested(lease):
                cancelled = lease_store.acknowledge_cancellation(lease, actor=actor)
                return {"status": "cancelled", "pipeline_run_id": pipeline_run_id,
                        "revision": cancelled["pipeline_revision"], "stage_results": stage_results}
        stage_context = {"input": deepcopy(input_context), "outputs": deepcopy(outputs)}
        input_hash = sha256_fingerprint(stage_context)
        try:
            result = execute_checkpointed_stage(
                registry, pipeline_run_id, ordinal, stage.name, stage_context,
                stage.handler, actor=actor, reason=f"stage_{ordinal}_execution",
                permission_check=stage.permission_check,
                provider_namespace=stage.provider_namespace,
                reconcile=stage.reconcile, replay_safe=stage.replay_safe,
                reconstruct=stage.reconstruct,
                lease_guard=(lambda: lease_store.assert_current(lease)) if lease_store is not None else None,
            )
        except StageCheckpointError:
            checkpoint = checkpoint_store.get(pipeline_run_id, ordinal)
            if checkpoint is None:
                raise
            if checkpoint.stage_name != stage.name or checkpoint.input_fingerprint != input_hash:
                raise
            if checkpoint.status == "succeeded":
                raise
            stage_results.append({
                "stage": stage.name, "status": checkpoint.status,
                "input_hash": input_hash,
            })
            if checkpoint.status in {"failed", "blocked"}:
                if lease_store is not None:
                    lease_store.assert_current(lease)
                    if lease_store.cancellation_requested(lease):
                        cancelled = lease_store.acknowledge_cancellation(lease, actor=actor)
                        return {"status": "cancelled", "pipeline_run_id": pipeline_run_id,
                                "revision": cancelled["pipeline_revision"], "stage_results": stage_results}
                latest = registry.get_pipeline_run(pipeline_run_id)
                record = dict(latest["record"])
                record.update(
                    status="failed",
                    finished_at=finished_at.isoformat(),
                    failure_code="stage_failed",
                    failure_detail="A checkpointed stage failed or was blocked; details are held in its checkpoint.",
                    stage_results=stage_results + [
                        {"stage": remaining.name, "status": "blocked"}
                        for remaining in stage_list[ordinal + 1:]
                    ],
                )
                registry.transition_pipeline_run(
                    pipeline_run_id, record, expected_revision=latest["revision"],
                    actor=actor, reason=f"{reason}_failed_stage_{ordinal}",
                )
                return {"status": "failed", "pipeline_run_id": pipeline_run_id,
                        "stage_results": record["stage_results"]}
            return {
                "status": "indeterminate" if checkpoint.status == "indeterminate" else "pending",
                "pipeline_run_id": pipeline_run_id,
                "stage_results": stage_results,
                "checkpoint_status": checkpoint.status,
            }
        outputs[stage.name] = result["output"]
        stage_results.append({
            "stage": stage.name, "status": "succeeded",
            "input_hash": input_hash,
            "output_hash": result["output_fingerprint"],
        })

    if lease_store is not None:
        lease_store.assert_current(lease)
        if lease_store.cancellation_requested(lease):
            cancelled = lease_store.acknowledge_cancellation(lease, actor=actor)
            return {"status": "cancelled", "pipeline_run_id": pipeline_run_id,
                    "revision": cancelled["pipeline_revision"], "stage_results": stage_results}
    latest = registry.get_pipeline_run(pipeline_run_id)
    if latest is None or latest["record"]["status"] != "running":
        raise StageCheckpointError("pipeline run changed before durable completion")
    started_at = datetime.fromisoformat(
        latest["record"]["started_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    if finished_at < started_at:
        finished_at = started_at
    record = dict(
        latest["record"],
        status="succeeded",
        finished_at=finished_at.isoformat(),
        output_hashes=sorted({item["output_hash"] for item in stage_results}),
        stage_results=stage_results,
    )
    completed = registry.transition_pipeline_run(
        pipeline_run_id, record, expected_revision=latest["revision"],
        actor=actor, reason=f"{reason}_completed",
    )
    return {
        "status": "succeeded", "pipeline_run_id": pipeline_run_id,
        "revision": completed["revision"], "stage_results": stage_results,
        "output_hashes": record["output_hashes"], "outputs": outputs,
    }
