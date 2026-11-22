from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.checkpoint_recovery import (
    CheckpointRecoveryError,
    CheckpointRecoveryPlan,
    recover_unresolved_checkpoints,
)
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.stage_checkpoints import Reconciliation, StageCheckpointStore
from sngso_engine.worker_leases import WorkerLeaseStore


def _running_run(registry, run_id="pipeline-run:recovery-test"):
    registry.add_pipeline_run({
        "schema_version": "1.0.0",
        "id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "recovery-test/1.0",
        "config_hash": "sha256:" + "c" * 64,
        "input_hashes": [sha256_fingerprint({"run": run_id})],
        "initiated_by": "test_scheduler",
    }, actor="test_scheduler", reason="create_recovery_test_run")


def _seed_indeterminate(registry, context, *, run_id="pipeline-run:recovery-test"):
    store = StageCheckpointStore(registry)
    checkpoint = store.begin(
        run_id, 0, "remote_write", sha256_fingerprint(context),
        provider_namespace="provider_x", actor="test_worker",
        reason="seed_interrupted_stage",
    )
    return store.transition(
        checkpoint, "indeterminate", actor="test_worker",
        reason="seed_provider_pending",
        provider_operation_ref="provider-operation-42",
        metadata={"reconciliation": "pending", "provider_state": "pending"},
    )


def _plan(context, *, handler, reconcile, replay_safe=False):
    return CheckpointRecoveryPlan(
        context=context,
        handler=handler,
        permission_check=lambda: True,
        reconcile=reconcile,
        replay_safe=replay_safe,
    )


def test_recovery_reconciles_confirmed_completion_without_replaying_handler():
    context = {"payload": {"document_id": "doc-42"}}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        checkpoint = _seed_indeterminate(registry, context)
        leases = WorkerLeaseStore(registry)

        def reconcile(key, operation_ref):
            calls.append((key, operation_ref))
            return Reconciliation(
                "completed", output={"provider_id": "remote-42"},
                operation_ref=operation_ref,
            )

        result = recover_unresolved_checkpoints(
            registry, leases, checkpoint.pipeline_run_id, "worker_recovery",
            authorize=lambda worker, run: worker == "worker_recovery",
            resolve_plan=lambda current: _plan(
                context, handler=lambda ctx, key: pytest.fail("completed provider operation must not replay"),
                reconcile=reconcile,
            ),
        )
        stored = StageCheckpointStore(registry).get(checkpoint.pipeline_run_id, 0)
        assert result["status"] == "completed"
        assert result["stages"][0]["status"] == "succeeded"
        assert result["stages"][0]["idempotency_key"] == checkpoint.idempotency_key
        assert stored.status == "succeeded"
        assert calls == [(checkpoint.idempotency_key, "provider-operation-42")]
        assert leases.history(checkpoint.pipeline_run_id)[-1]["status"] == "released"


def test_recovery_replays_only_after_provider_confirms_not_started():
    context = {"payload": {"document_id": "doc-43"}}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        checkpoint = _seed_indeterminate(registry, context)
        leases = WorkerLeaseStore(registry)

        def reconcile(key, operation_ref):
            assert key == checkpoint.idempotency_key
            assert operation_ref == "provider-operation-42"
            return Reconciliation("not_started", operation_ref=operation_ref)

        def handler(current, key):
            calls.append(key)
            return {"created": "once"}

        result = recover_unresolved_checkpoints(
            registry, leases, checkpoint.pipeline_run_id, "worker_recovery",
            authorize=lambda worker, run: True,
            resolve_plan=lambda current: _plan(context, handler=handler, reconcile=reconcile),
        )
        stored = StageCheckpointStore(registry).get(checkpoint.pipeline_run_id, 0)
        assert result["status"] == "completed"
        assert calls == [checkpoint.idempotency_key]
        assert stored.status == "succeeded"


def test_recovery_pending_outcome_stops_without_replay_and_keeps_operation_reference():
    context = {"payload": {"document_id": "doc-44"}}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        checkpoint = _seed_indeterminate(registry, context)
        leases = WorkerLeaseStore(registry)
        result = recover_unresolved_checkpoints(
            registry, leases, checkpoint.pipeline_run_id, "worker_recovery",
            authorize=lambda worker, run: True,
            resolve_plan=lambda current: _plan(
                context,
                handler=lambda ctx, key: pytest.fail("pending operation must not replay"),
                reconcile=lambda key, ref: Reconciliation("pending", operation_ref=ref),
            ),
        )
        stored = StageCheckpointStore(registry).get(checkpoint.pipeline_run_id, 0)
        assert result["status"] == "unresolved"
        assert result["stages"][0]["status"] == "indeterminate"
        assert stored.provider_operation_ref == "provider-operation-42"


def test_recovery_context_fingerprint_mismatch_fails_closed_before_provider_call():
    context = {"payload": {"document_id": "doc-45"}}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        checkpoint = _seed_indeterminate(registry, context)
        leases = WorkerLeaseStore(registry)
        provider_calls = []
        result = recover_unresolved_checkpoints(
            registry, leases, checkpoint.pipeline_run_id, "worker_recovery",
            authorize=lambda worker, run: True,
            resolve_plan=lambda current: _plan(
                {"payload": {"document_id": "different"}},
                handler=lambda ctx, key: {"bad": True},
                reconcile=lambda key, ref: provider_calls.append(key),
            ),
        )
        stored = StageCheckpointStore(registry).get(checkpoint.pipeline_run_id, 0)
        assert result["status"] == "context_fingerprint_mismatch"
        assert provider_calls == []
        assert stored.status == "indeterminate"


def test_recovery_authorization_denial_does_not_acquire_a_lease():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        leases = WorkerLeaseStore(registry)
        with pytest.raises(CheckpointRecoveryError, match="authorization denied"):
            recover_unresolved_checkpoints(
                registry, leases, "pipeline-run:recovery-test", "worker_denied",
                authorize=lambda worker, run: False,
                resolve_plan=lambda checkpoint: pytest.fail("plan must not be resolved"),
            )
        assert leases.history("pipeline-run:recovery-test") == []


def test_recovery_lease_loss_during_provider_lookup_leaves_checkpoint_unresolved():
    context = {"payload": {"document_id": "doc-46"}}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        checkpoint = _seed_indeterminate(registry, context)
        leases = WorkerLeaseStore(registry)

        def reconcile(key, operation_ref):
            leases.acquire(
                checkpoint.pipeline_run_id, "worker_takeover",
                ttl_seconds=60, actor="worker_takeover", reason="test_takeover",
                now=datetime.now(timezone.utc) + timedelta(seconds=11),
            )
            return Reconciliation("completed", output={"remote": "maybe" }, operation_ref=operation_ref)

        with pytest.raises(CheckpointRecoveryError, match="lease was lost"):
            recover_unresolved_checkpoints(
                registry, leases, checkpoint.pipeline_run_id, "worker_recovery",
                ttl_seconds=10, heartbeat_interval_seconds=4,
                authorize=lambda worker, run: True,
                resolve_plan=lambda current: _plan(
                    context,
                    handler=lambda ctx, key: pytest.fail("lost lease must not reach handler"),
                    reconcile=reconcile,
                ),
            )
        stored = StageCheckpointStore(registry).get(checkpoint.pipeline_run_id, 0)
        assert stored.status == "indeterminate"
        assert stored.provider_operation_ref == "provider-operation-42"
        assert leases.history(checkpoint.pipeline_run_id)[-1]["worker_id"] == "worker_takeover"
