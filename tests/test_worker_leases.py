from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from threading import Barrier

import pytest

from sngso_engine import (
    CheckpointedStage,
    LeaseConflictError,
    LeaseExpiredError,
    LeaseLostError,
    SQLiteRegistry,
    WorkerLeaseError,
    WorkerLeaseStore,
    execute_checkpointed_pipeline,
    sha256_fingerprint,
)


def _running(registry, run_id="pipeline-run:lease-test"):
    context = {"batch": "lease-fixture"}
    payload = {
        "schema_version": "1.0.0",
        "id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "lease-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": [sha256_fingerprint(context)],
        "initiated_by": "test-scheduler",
    }
    registry.add_pipeline_run(payload, actor="test_scheduler", reason="create_running_lease_test")
    return context


def test_lease_acquire_persists_only_token_hash_and_blocks_second_live_owner():
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=60,
        )
        assert lease.fencing_token == 1
        assert lease.status == "active"
        row = registry._connection.execute(
            "SELECT token_hash, worker_id, fencing_token FROM pipeline_worker_leases"
        ).fetchone()
        assert row["token_hash"] == sha256_fingerprint(lease.token)
        assert lease.token not in row["token_hash"]
        with pytest.raises(LeaseConflictError):
            store.acquire(
                "pipeline-run:lease-test", "worker_two", actor="scheduler",
                reason="duplicate_claim", ttl_seconds=60,
            )


def test_heartbeat_extends_expiry_and_appends_immutable_revision_history():
    now = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=30, now=now,
        )
        renewed = store.heartbeat(
            lease, actor="worker_one", ttl_seconds=90,
            now=now + timedelta(seconds=20),
        )
        assert renewed.fencing_token == lease.fencing_token
        assert renewed.revision == 2
        assert renewed.expires_at > lease.expires_at
        assert len(store.history("pipeline-run:lease-test")) == 2
        assert store.assert_current(renewed, now=now + timedelta(seconds=21)) is None


def test_expired_lease_can_be_taken_over_with_new_fencing_generation():
    now = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        first = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10, now=now,
        )
        second = store.acquire(
            "pipeline-run:lease-test", "worker_two", actor="scheduler",
            reason="takeover_after_expiry", ttl_seconds=30,
            now=now + timedelta(seconds=11),
        )
        assert second.fencing_token == first.fencing_token + 1
        assert second.worker_id == "worker_two"
        with pytest.raises(LeaseLostError):
            store.assert_current(first, now=now + timedelta(seconds=12))
        assert [item["status"] for item in store.history("pipeline-run:lease-test")] == [
            "active", "expired", "active",
        ]


def test_expired_owner_cannot_heartbeat_or_release():
    now = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10, now=now,
        )
        with pytest.raises(LeaseExpiredError):
            store.heartbeat(lease, actor="worker_one", now=now + timedelta(seconds=11))
        with pytest.raises(LeaseExpiredError):
            store.release(lease, actor="worker_one", now=now + timedelta(seconds=11))


def test_released_lease_can_be_reacquired_but_old_token_is_fenced_out():
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        first = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )
        store.release(first, actor="worker_one")
        second = store.acquire(
            "pipeline-run:lease-test", "worker_two", actor="scheduler",
            reason="operator_handoff",
        )
        assert second.fencing_token == first.fencing_token + 1
        with pytest.raises(LeaseLostError):
            store.heartbeat(first, actor="worker_one")


def test_cancellation_is_cooperative_and_acknowledgement_cancels_pipeline():
    context = {"batch": "lease-fixture"}
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )
        requested = store.request_cancellation(
            "pipeline-run:lease-test", actor="operator",
            reason_code="operator_requested",
        )
        assert requested["requested"] is True
        assert store.request_cancellation(
            "pipeline-run:lease-test", actor="operator",
            reason_code="operator_requested",
        )["already_requested"] is True
        assert store.cancellation_requested(lease) is True
        called = []
        result = execute_checkpointed_pipeline(
            registry, "pipeline-run:lease-test", input_context=context,
            stages=[CheckpointedStage(
                "collect", lambda current, key: called.append("ran") or {"ok": True},
                permission_check=lambda: True, replay_safe=True,
            )],
            actor="worker_one", reason="cooperative_cancel_test",
            lease_store=store, lease=lease,
        )
        assert result["status"] == "cancelled"
        assert called == []
        assert registry.get_pipeline_run("pipeline-run:lease-test")["record"]["status"] == "cancelled"
        assert store.history("pipeline-run:lease-test")[-1]["status"] == "cancelled"


def test_cancellation_requested_during_stage_stops_before_next_stage():
    context = {"batch": "lease-fixture"}
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )
        called = []

        def first_stage(current, key):
            called.append("first")
            store.request_cancellation(
                "pipeline-run:lease-test", actor="operator",
                reason_code="operator_requested",
            )
            return {"done": True}

        result = execute_checkpointed_pipeline(
            registry, "pipeline-run:lease-test", input_context=context,
            stages=[
                CheckpointedStage("first", first_stage, permission_check=lambda: True, replay_safe=True),
                CheckpointedStage(
                    "second", lambda current, key: called.append("second") or {"bad": True},
                    permission_check=lambda: True, replay_safe=True,
                ),
            ],
            actor="worker_one", reason="cancel_between_stages",
            lease_store=store, lease=lease,
        )
        assert result["status"] == "cancelled"
        assert called == ["first"]
        run = registry.get_pipeline_run("pipeline-run:lease-test")
        assert run["record"]["status"] == "cancelled"


def test_lease_guard_stops_stage_handler_after_ownership_is_lost():
    from sngso_engine.stage_checkpoints import execute_checkpointed_stage
    from sngso_engine.worker_leases import LeaseLostError

    context = {"batch": "lease-fixture"}
    called = []
    with SQLiteRegistry() as registry:
        _running(registry)
        with pytest.raises(LeaseLostError):
            execute_checkpointed_stage(
                registry, "pipeline-run:lease-test", 0, "collect", context,
                lambda current, key: called.append("ran"),
                actor="worker_one", reason="lease_guard_test",
                permission_check=lambda: True, replay_safe=True,
                lease_guard=lambda: (_ for _ in ()).throw(LeaseLostError("fenced")),
            )
        assert called == []


def test_two_workers_cannot_acquire_same_unexpired_lease_concurrently(tmp_path):
    database = tmp_path / "worker-leases.sqlite"
    with SQLiteRegistry(database) as registry:
        _running(registry)
    barrier = Barrier(2)

    def attempt(worker_id):
        with SQLiteRegistry(database) as registry:
            store = WorkerLeaseStore(registry)
            barrier.wait(timeout=5)
            try:
                return store.acquire(
                    "pipeline-run:lease-test", worker_id, actor="scheduler",
                    reason="concurrent_claim", ttl_seconds=60,
                ).worker_id
            except LeaseConflictError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["worker_one", "worker_two"]))
    assert sorted(results).count("conflict") == 1
    assert sum(value != "conflict" for value in results) == 1


def test_worker_lease_schema_migrates_from_v32(tmp_path, monkeypatch):
    from sngso_engine import registry as registry_module

    database = tmp_path / "worker-lease-v32.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 32)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 32
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 33)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 33
        names = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "pipeline_worker_leases" in names
        assert "pipeline_worker_lease_revisions" in names


def test_sqlite_rejects_worker_lease_revision_tampering():
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE pipeline_worker_lease_revisions SET reason = 'tampered' WHERE revision = 1"
            )
