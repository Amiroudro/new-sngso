"""Authenticated, lease-bound recovery of interrupted checkpointed stages.

Recovery reconstructs stage inputs from trusted application state, verifies the
exact persisted input fingerprint, then delegates replay decisions to the
checkpoint runner and provider reconciliation contract. It never guesses or
recreates missing stage context from checkpoint metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .canonical import sha256_fingerprint
from .stage_checkpoints import (
    Reconciliation,
    StageCheckpoint,
    StageCheckpointError,
    StageCheckpointStore,
    execute_checkpointed_stage,
)
from .worker_leases import (
    LeaseLostError,
    WorkerLeaseError,
    WorkerLeaseSession,
    WorkerLeaseStore,
)


class CheckpointRecoveryError(RuntimeError):
    """Recovery could not safely claim or inspect the requested pipeline run."""


@dataclass(frozen=True)
class CheckpointRecoveryPlan:
    """Trusted in-memory stage inputs and callbacks needed to resume one stage."""

    context: dict[str, Any]
    handler: Callable[[dict[str, Any], str], Any]
    permission_check: Callable[[], bool]
    reconcile: Callable[[str, str | None], Reconciliation] | None = None
    replay_safe: bool = False


def recover_unresolved_checkpoints(
    registry: Any,
    lease_store: WorkerLeaseStore,
    pipeline_run_id: str,
    worker_id: str,
    *,
    authorize: Callable[[str, str], bool],
    resolve_plan: Callable[[StageCheckpoint], CheckpointRecoveryPlan],
    ttl_seconds: int = 60,
    heartbeat_interval_seconds: float | None = None,
) -> dict[str, Any]:
    """Acquire a fresh lease and safely reconcile unresolved stages in order.

    authorize must authenticate/authorize the worker for this run and return
    the literal boolean True. resolve_plan reconstructs original stage context
    from trusted application state. A context mismatch stops recovery before
    the checkpoint runner or provider is called.

    Provider reconciliation is wrapped with lease checks before and after the
    remote lookup. Unknown/pending outcomes stop the ordered recovery pass.
    Only the checkpoint runner may authorize a retry, after provider-confirmed
    not_started or an explicitly replay-safe plan.
    """
    if not isinstance(lease_store, WorkerLeaseStore) or lease_store.registry is not registry:
        raise TypeError("lease_store must belong to the supplied registry")
    if not callable(authorize) or not callable(resolve_plan):
        raise TypeError("authorize and resolve_plan callbacks are required")
    try:
        permitted = authorize(worker_id, pipeline_run_id) is True
    except Exception:
        permitted = False
    if not permitted:
        raise CheckpointRecoveryError("worker authorization denied checkpoint recovery")

    try:
        lease = lease_store.acquire(
            pipeline_run_id, worker_id, ttl_seconds=ttl_seconds,
            actor=worker_id, reason="restart_recovery",
        )
    except WorkerLeaseError as exc:
        raise CheckpointRecoveryError("worker could not acquire the recovery lease") from exc

    interval = heartbeat_interval_seconds
    if interval is None:
        interval = min(10.0, ttl_seconds / 3.0)
    session = WorkerLeaseSession(
        lease_store, lease, heartbeat_interval_seconds=interval,
        ttl_seconds=ttl_seconds,
    ).start()
    store = StageCheckpointStore(registry)
    outcomes: list[dict[str, Any]] = []
    final_status = "completed"
    try:
        session.assert_healthy()
        candidates = store.list_unresolved(pipeline_run_id)
        for candidate in candidates:
            session.raise_if_cancelled()
            current = store.get(candidate.pipeline_run_id, candidate.stage_ordinal)
            if current is None or current.status not in {"executing", "indeterminate"}:
                continue
            try:
                plan = resolve_plan(current)
            except Exception:
                final_status = "plan_unavailable"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": "plan_unavailable",
                })
                break
            if not isinstance(plan, CheckpointRecoveryPlan):
                final_status = "invalid_plan"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": "invalid_plan",
                })
                break
            if not isinstance(plan.context, dict) or (
                sha256_fingerprint(plan.context) != current.input_fingerprint
            ):
                final_status = "context_fingerprint_mismatch"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": "context_fingerprint_mismatch",
                })
                break
            if not callable(plan.handler) or not callable(plan.permission_check):
                final_status = "invalid_plan"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": "invalid_plan",
                })
                break
            if not isinstance(plan.replay_safe, bool):
                final_status = "invalid_plan"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": "invalid_plan",
                })
                break
            if plan.reconcile is not None and not callable(plan.reconcile):
                final_status = "invalid_plan"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": "invalid_plan",
                })
                break

            guarded_reconcile = None
            if plan.reconcile is not None:
                def guarded_reconcile(key: str, operation_ref: str | None) -> Reconciliation:
                    session.assert_healthy()
                    resolution = plan.reconcile(key, operation_ref)
                    session.assert_healthy()
                    return resolution

            try:
                result = execute_checkpointed_stage(
                    registry,
                    pipeline_run_id,
                    current.stage_ordinal,
                    current.stage_name,
                    plan.context,
                    plan.handler,
                    actor=worker_id,
                    reason="restart_recovery",
                    permission_check=plan.permission_check,
                    provider_namespace=current.provider_namespace,
                    reconcile=guarded_reconcile,
                    replay_safe=plan.replay_safe,
                    lease_guard=session.assert_healthy,
                )
            except StageCheckpointError:
                latest = store.get(pipeline_run_id, current.stage_ordinal)
                stage_status = latest.status if latest is not None else "missing"
                outcomes.append({
                    "stage_ordinal": current.stage_ordinal,
                    "stage_name": current.stage_name,
                    "status": stage_status,
                    "idempotency_key": current.idempotency_key,
                })
                final_status = "unresolved" if stage_status in {"executing", "indeterminate"} else stage_status
                break

            outcomes.append({
                "stage_ordinal": current.stage_ordinal,
                "stage_name": current.stage_name,
                "status": result["status"],
                "idempotency_key": result["idempotency_key"],
                "output_fingerprint": result["output_fingerprint"],
                "reused": result["reused"],
            })
        session.assert_healthy()
    except LeaseLostError as exc:
        final_status = "lease_lost"
        raise CheckpointRecoveryError("recovery lease was lost; unresolved stages require reconciliation") from exc
    finally:
        try:
            session.close(release=session.heartbeat_error is None)
        except WorkerLeaseError:
            pass

    return {
        "pipeline_run_id": pipeline_run_id,
        "worker_id": worker_id,
        "fencing_token": lease.fencing_token,
        "status": final_status,
        "stages": outcomes,
    }
