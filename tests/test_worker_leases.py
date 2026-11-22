from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from threading import Barrier

import pytest

from sngso_engine import (
    CancellationRequested,
    CheckpointedStage,
    LeaseConflictError,
    LeaseExpiredError,
    LeaseLostError,
    SQLiteRegistry,
    WorkerLeaseError,
    WorkerLeaseStore,
    WorkerLeaseSession,
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


def test_independent_registry_connections_share_one_file_lease_authority(tmp_path):
    """Separate SQLite connections serialize claims against the same local file."""
    database = tmp_path / "shared-worker-leases.sqlite"
    with SQLiteRegistry(database) as initializer:
        _running(initializer)
    with SQLiteRegistry(database) as registry_one, SQLiteRegistry(database) as registry_two:
        store_one = WorkerLeaseStore(registry_one)
        store_two = WorkerLeaseStore(registry_two)
        barrier = Barrier(2)

        def claim(store, worker_id):
            barrier.wait(timeout=5)
            try:
                return store.acquire(
                    "pipeline-run:lease-test", worker_id, actor="scheduler",
                    reason="independent_connection_claim", ttl_seconds=60,
                )
            except LeaseConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(claim, store_one, "worker_one"),
                pool.submit(claim, store_two, "worker_two"),
            ]
            results = [future.result(timeout=15) for future in futures]

        winners = [lease for lease in results if lease is not None]
        assert len(winners) == 1
        assert winners[0].fencing_token == 1
        with pytest.raises(LeaseConflictError):
            (store_two if winners[0].worker_id == "worker_one" else store_one).acquire(
                "pipeline-run:lease-test", "late_worker", actor="scheduler",
                reason="verify_shared_file_authority", ttl_seconds=60,
            )


def test_independent_registry_connection_observes_takeover_and_fences_old_owner(tmp_path):
    database = tmp_path / "shared-worker-takeover.sqlite"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    with SQLiteRegistry(database) as initializer:
        _running(initializer)
    with SQLiteRegistry(database) as registry_one, SQLiteRegistry(database) as registry_two:
        first = WorkerLeaseStore(registry_one).acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10, now=now,
        )
        second = WorkerLeaseStore(registry_two).acquire(
            "pipeline-run:lease-test", "worker_two", actor="scheduler",
            reason="takeover_after_expiry", ttl_seconds=30,
            now=now + timedelta(seconds=11),
        )
        assert second.fencing_token == first.fencing_token + 1
        with pytest.raises(LeaseLostError):
            WorkerLeaseStore(registry_one).assert_current(
                first, now=now + timedelta(seconds=12),
            )
        history = WorkerLeaseStore(registry_one).history("pipeline-run:lease-test")
        assert [item["status"] for item in history] == ["active", "expired", "active"]


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
            reason_code="operator_requested", permission_check=lambda: True,
        )
        assert requested["requested"] is True
        assert store.request_cancellation(
            "pipeline-run:lease-test", actor="operator",
            reason_code="operator_requested", permission_check=lambda: True,
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


def test_pending_cancellation_survives_expired_worker_handoff():
    now = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        first = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10, now=now,
        )
        store.request_cancellation(
            "pipeline-run:lease-test", actor="operator",
            reason_code="operator_requested", permission_check=lambda: True, now=now + timedelta(seconds=1),
        )
        second = store.acquire(
            "pipeline-run:lease-test", "worker_two", actor="scheduler",
            reason="takeover_after_expiry", ttl_seconds=60,
            now=now + timedelta(seconds=11),
        )
        assert second.fencing_token == first.fencing_token + 1
        assert second.cancellation_requested is True
        assert store.cancellation_requested(second, now=now + timedelta(seconds=12)) is True
        with pytest.raises(LeaseLostError):
            store.assert_current(first, now=now + timedelta(seconds=12))


def test_cancellation_request_fails_closed_without_authorized_permission():
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )
        with pytest.raises(WorkerLeaseError, match="permission check denied"):
            store.request_cancellation(
                "pipeline-run:lease-test", actor="operator",
                reason_code="operator_requested", permission_check=lambda: False,
            )
        assert store.cancellation_requested(lease) is False


def test_cancellation_request_fields_cannot_be_rewritten_in_sql():
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )
        store.request_cancellation(
            "pipeline-run:lease-test", actor="operator",
            reason_code="operator_requested", permission_check=lambda: True,
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable once recorded"):
            registry._connection.execute(
                "UPDATE pipeline_worker_leases SET cancel_reason_code = 'changed', "
                "revision = revision + 1, updated_at = '2026-11-22T12:00:00Z' "
                "WHERE pipeline_run_id = 'pipeline-run:lease-test'"
            )


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
                reason_code="operator_requested", permission_check=lambda: True,
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


def test_lease_lost_during_handler_leaves_checkpoint_for_provider_reconciliation():
    from sngso_engine.stage_checkpoints import (
        Reconciliation, StageCheckpointError, StageCheckpointStore,
        execute_checkpointed_stage,
    )

    context = {"batch": "lease-fixture"}
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        first = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim",
        )

        def side_effect(current, key):
            # Simulate the worker losing its lease after the provider accepted the operation.
            store.release(first, actor="worker_one", reason="simulated_lease_loss")
            return {"provider_receipt": "accepted"}

        with pytest.raises(LeaseLostError):
            execute_checkpointed_stage(
                registry, "pipeline-run:lease-test", 0, "remote_write", context,
                side_effect, actor="worker_one", reason="lease_loss_during_effect",
                permission_check=lambda: True, provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation("unknown"),
                lease_guard=lambda: store.assert_current(first),
            )
        checkpoint = StageCheckpointStore(registry).get("pipeline-run:lease-test", 0)
        assert checkpoint.status == "executing"

        second = store.acquire(
            "pipeline-run:lease-test", "worker_two", actor="scheduler",
            reason="recovery_worker_claim",
        )
        recovered = execute_checkpointed_stage(
            registry, "pipeline-run:lease-test", 0, "remote_write", context,
            lambda current, key: pytest.fail("reconciliation must prevent duplicate side effect"),
            actor="worker_two", reason="reconcile_accepted_effect",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=lambda key, ref: Reconciliation(
                "completed", output={"provider_receipt": "accepted"},
            ),
            lease_guard=lambda: store.assert_current(second),
        )
        assert recovered["status"] == "succeeded"
        assert recovered["reused"] is True


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


def test_worker_lease_session_maintains_heartbeat_and_releases_on_exit():
    import time

    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10,
        )
        with WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ) as session:
            time.sleep(0.22)
            session.assert_healthy()
            assert session.lease.revision >= 3
        assert store.history("pipeline-run:lease-test")[-1]["status"] == "released"


def test_worker_lease_session_propagates_cooperative_cancellation():
    import time

    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10,
        )
        store.request_cancellation(
            "pipeline-run:lease-test", actor="operator",
            reason_code="operator_requested", permission_check=lambda: True,
        )
        with WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ) as session:
            time.sleep(0.12)
            assert session.check_cancellation() is True
            assert session.cancellation_event.is_set()
            with pytest.raises(CancellationRequested):
                session.raise_if_cancelled()


def test_worker_lease_session_rejects_unsafe_heartbeat_interval():
    with SQLiteRegistry() as registry:
        _running(registry)
        store = WorkerLeaseStore(registry)
        lease = store.acquire(
            "pipeline-run:lease-test", "worker_one", actor="scheduler",
            reason="initial_claim", ttl_seconds=10,
        )
        with pytest.raises(ValueError, match="less than half"):
            WorkerLeaseSession(
                store, lease, heartbeat_interval_seconds=5, ttl_seconds=10,
            )
        with pytest.raises(ValueError, match="less than half"):
            WorkerLeaseSession(
                store, lease, heartbeat_interval_seconds=float("nan"), ttl_seconds=10,
            )


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
