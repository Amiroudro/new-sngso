from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.provider_fencing import (
    ProviderCommand,
    ProviderFenceError,
    execute_fenced_provider_command,
)
from sngso_engine.registry import SQLiteRegistry
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
