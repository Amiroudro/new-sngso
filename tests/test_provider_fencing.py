from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.provider_fencing import (
    ProviderCommand,
    ProviderFenceError,
    execute_fenced_provider_command,
    fenced_provider_stage_handler,
)
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.stage_checkpoints import (
    Reconciliation,
    StageCheckpointError,
    StageCheckpointStore,
    execute_checkpointed_stage,
)
from sngso_engine.worker_leases import LeaseLostError, WorkerLeaseStore


def _running_run(registry, run_id="pipeline-run:provider-fence-test"):
    payload = {
        "schema_version": "1.0.0",
        "id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "engine-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": [sha256_fingerprint({"fixture": True})],
        "initiated_by": "test-scheduler",
    }
    registry.add_pipeline_run(payload, actor="test_scheduler", reason="create_fencing_test_run")


class FakeFencedProvider:
    """Contract fixture modelling provider-atomic generation and key checks."""

    def __init__(self):
        self.highest_generation = {}
        self.results = {}
        self.calls = 0

    def execute_fenced(self, command, request):
        resource = (command.provider_namespace, command.pipeline_run_id)
        highest = self.highest_generation.get(resource, 0)
        if command.fencing_token < highest:
            raise ProviderFenceError("stale fencing generation")
        self.highest_generation[resource] = max(highest, command.fencing_token)
        existing = self.results.get(command.idempotency_key)
        if existing is not None:
            fingerprint, result = existing
            if fingerprint != command.request_fingerprint:
                raise ProviderFenceError("idempotency key reused for a different request")
            return result
        self.calls += 1
        result = {"provider_receipt": f"receipt-{self.calls}"}
        self.results[command.idempotency_key] = (command.request_fingerprint, result)
        return result


def _command(generation, request, *, key="sha256:" + "b" * 64):
    return ProviderCommand(
        provider_namespace="provider_x",
        pipeline_run_id="pipeline-run:provider-fence-test",
        stage_ordinal=0,
        idempotency_key=key,
        fencing_token=generation,
        request_fingerprint=sha256_fingerprint(request),
    )


def test_provider_contract_rejects_stale_generation_after_new_owner_is_accepted():
    provider = FakeFencedProvider()
    request = {"operation": "write", "value": 1}
    assert provider.execute_fenced(_command(2, request), request)["provider_receipt"] == "receipt-1"
    with pytest.raises(ProviderFenceError, match="stale fencing"):
        provider.execute_fenced(_command(1, {"operation": "write", "value": 2}), {"operation": "write", "value": 2})
    assert provider.calls == 1


def test_provider_contract_deduplicates_exact_key_and_rejects_key_payload_conflict():
    provider = FakeFencedProvider()
    request = {"operation": "write", "value": 1}
    command = _command(3, request)
    first = provider.execute_fenced(command, request)
    second = provider.execute_fenced(command, request)
    assert first == second
    assert provider.calls == 1
    different = {"operation": "write", "value": 999}
    with pytest.raises(ProviderFenceError, match="different request"):
        provider.execute_fenced(_command(3, different), different)
    assert provider.calls == 1


def test_dispatch_wrapper_carries_lease_generation_and_checks_ownership_before_and_after():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        leases = WorkerLeaseStore(registry)
        now = datetime.now(timezone.utc)
        lease = leases.acquire(
            "pipeline-run:provider-fence-test", "worker_one", ttl_seconds=10,
            actor="worker_one", reason="start_worker", now=now,
        )
        provider = FakeFencedProvider()
        result = execute_fenced_provider_command(
            provider, leases, lease,
            provider_namespace="provider_x", stage_ordinal=0,
            idempotency_key="sha256:" + "c" * 64,
            request={"operation": "publish", "item": "safe-id"},
        )
        assert result == {"provider_receipt": "receipt-1"}
        assert provider.highest_generation[("provider_x", lease.pipeline_run_id)] == lease.fencing_token


def test_dispatch_wrapper_refuses_old_lease_after_takeover_without_calling_provider():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        leases = WorkerLeaseStore(registry)
        now = datetime.now(timezone.utc)
        old = leases.acquire(
            "pipeline-run:provider-fence-test", "worker_one", ttl_seconds=10,
            actor="worker_one", reason="start_worker", now=now,
        )
        new = leases.acquire(
            "pipeline-run:provider-fence-test", "worker_two", ttl_seconds=10,
            actor="worker_two", reason="takeover",
            now=now + timedelta(seconds=11),
        )
        assert new.fencing_token == old.fencing_token + 1
        provider = FakeFencedProvider()
        with pytest.raises(LeaseLostError):
            execute_fenced_provider_command(
                provider, leases, old,
                provider_namespace="provider_x", stage_ordinal=0,
                idempotency_key="sha256:" + "d" * 64,
                request={"operation": "publish", "item": "safe-id"},
            )
        assert provider.calls == 0



def test_fenced_provider_stage_handler_integrates_checkpoint_idempotency_and_lease_generation():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        leases = WorkerLeaseStore(registry)
        lease = leases.acquire(
            "pipeline-run:provider-fence-test", "worker_one", ttl_seconds=60,
            actor="worker_one", reason="start_worker",
        )
        provider = FakeFencedProvider()
        handler = fenced_provider_stage_handler(
            provider, leases, lease,
            provider_namespace="provider_x",
            stage_ordinal=0,
            request_builder=lambda context: {"value": context["value"]},
        )
        result = execute_checkpointed_stage(
            registry, "pipeline-run:provider-fence-test", 0, "remote_write",
            {"value": "safe"},
            handler,
            actor="worker_one", reason="fenced_provider_stage",
            permission_check=lambda: True,
            provider_namespace="provider_x",
            lease_guard=lambda: leases.assert_current(lease),
        )
        assert result["status"] == "succeeded"
        assert result["output"] == {"provider_receipt": "receipt-1"}
        assert provider.calls == 1
        assert provider.highest_generation[("provider_x", lease.pipeline_run_id)] == lease.fencing_token



def test_reconciliation_reference_drift_fails_closed_and_preserves_original_handle():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        checkpoint = store.begin(
            "pipeline-run:provider-fence-test", 0, "remote_write",
            sha256_fingerprint({"value": "safe"}),
            provider_namespace="provider_x",
            actor="test_scheduler", reason="create_checkpoint",
        )
        checkpoint = store.transition(
            checkpoint, "indeterminate",
            actor="test_scheduler", reason="provider_pending",
            provider_operation_ref="operation-original",
            metadata={"reconciliation": "pending", "provider_state": "pending"},
        )

        def handler(context, key):
            raise AssertionError("a mismatched reconciliation reference must never replay")

        with pytest.raises(StageCheckpointError, match="provider outcome is unknown"):
            execute_checkpointed_stage(
                registry, "pipeline-run:provider-fence-test", 0, "remote_write",
                {"value": "safe"}, handler,
                actor="test_scheduler", reason="reconcile_reference_drift",
                permission_check=lambda: True,
                provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation(
                    "completed", output={"receipt": "untrusted-operation"},
                    operation_ref="operation-different",
                ),
            )

        current = store.get("pipeline-run:provider-fence-test", 0)
        assert current.status == "indeterminate"
        assert current.provider_operation_ref == "operation-original"


@pytest.mark.parametrize("bad_reference", ["contains spaces", "x" * 257])
def test_malformed_provider_operation_reference_is_unknown_not_a_local_transition_error(bad_reference):
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        checkpoint = store.begin(
            "pipeline-run:provider-fence-test", 0, "remote_write",
            sha256_fingerprint({"value": "safe"}),
            provider_namespace="provider_x",
            actor="test_scheduler", reason="create_checkpoint",
        )
        checkpoint = store.transition(
            checkpoint, "indeterminate",
            actor="test_scheduler", reason="provider_pending",
            provider_operation_ref="operation-original",
            metadata={"reconciliation": "pending", "provider_state": "pending"},
        )

        with pytest.raises(StageCheckpointError, match="provider outcome is unknown"):
            execute_checkpointed_stage(
                registry, "pipeline-run:provider-fence-test", 0, "remote_write",
                {"value": "safe"}, lambda context, key: pytest.fail("must not replay"),
                actor="test_scheduler", reason="reconcile_malformed_reference",
                permission_check=lambda: True,
                provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation(
                    "pending", operation_ref=bad_reference,
                ),
            )

        current = store.get("pipeline-run:provider-fence-test", 0)
        assert current.status == "indeterminate"
        assert current.provider_operation_ref == "operation-original"



def test_lease_loss_during_provider_call_keeps_checkpoint_unresolved_for_reconciliation():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        leases = WorkerLeaseStore(registry)
        now = datetime.now(timezone.utc)
        old_lease = leases.acquire(
            "pipeline-run:provider-fence-test", "worker_one", ttl_seconds=10,
            actor="worker_one", reason="start_worker", now=now,
        )

        class ProviderThatCompletesAfterTakeover:
            calls = 0

            def execute_fenced(self, command, request):
                self.calls += 1
                # Model a remote side effect completing while the original
                # worker's lease expires and a new owner takes over.
                leases.acquire(
                    "pipeline-run:provider-fence-test", "worker_two",
                    ttl_seconds=60, actor="worker_two", reason="takeover",
                    now=now + timedelta(seconds=11),
                )
                return {"provider_receipt": "remote-completed"}

        provider = ProviderThatCompletesAfterTakeover()
        handler = fenced_provider_stage_handler(
            provider, leases, old_lease,
            provider_namespace="provider_x", stage_ordinal=0,
            request_builder=lambda context: {"value": context["value"]},
        )

        with pytest.raises(LeaseLostError):
            execute_checkpointed_stage(
                registry, "pipeline-run:provider-fence-test", 0, "remote_write",
                {"value": "safe"}, handler,
                actor="worker_one", reason="provider_call_lost_lease",
                permission_check=lambda: True,
                provider_namespace="provider_x",
                lease_guard=lambda: leases.assert_current(old_lease),
            )

        checkpoint = StageCheckpointStore(registry).get(
            "pipeline-run:provider-fence-test", 0,
        )
        assert provider.calls == 1
        assert checkpoint.status == "executing"
        assert checkpoint.output_fingerprint is None
        assert checkpoint.provider_operation_ref is None
