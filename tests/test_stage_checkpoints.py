from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.operations import create_pipeline_run_idempotently
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.stage_checkpoints import (
    CheckpointedStage,
    Reconciliation,
    StageCheckpointError,
    StageCheckpointStore,
    execute_checkpointed_pipeline,
    execute_checkpointed_stage,
    stage_idempotency_key,
)


def _running_run(registry, *, run_id="pipeline-run:checkpoint-test"):
    context = {"batch": "safe-fixture"}
    payload = {
        "schema_version": "1.0.0",
        "id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "engine-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": [sha256_fingerprint(context)],
        "initiated_by": "test-scheduler",
    }
    return registry.add_pipeline_run(payload, actor="test_scheduler", reason="create_running_test_run")


def test_checkpointed_pipeline_executes_in_order_and_completes_run():
    context = {"batch": "safe-fixture"}
    observed = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        result = execute_checkpointed_pipeline(
            registry, "pipeline-run:checkpoint-test",
            input_context=context,
            stages=[
                CheckpointedStage(
                    "collect",
                    lambda current, key: observed.append("collect") or {"count": 2},
                    permission_check=lambda: True,
                    replay_safe=True,
                ),
                CheckpointedStage(
                    "normalize",
                    lambda current, key: observed.append("normalize") or {
                        "normalized_count": current["outputs"]["collect"]["count"]
                    },
                    permission_check=lambda: True,
                    replay_safe=True,
                ),
            ],
            actor="test_worker", reason="checkpointed_pipeline_test",
        )
        stored = registry.get_pipeline_run("pipeline-run:checkpoint-test")
        assert result["status"] == "succeeded"
        assert result["outputs"]["normalize"] == {"normalized_count": 2}
        assert observed == ["collect", "normalize"]
        assert stored["record"]["status"] == "succeeded"
        assert [item["status"] for item in stored["record"]["stage_results"]] == [
            "succeeded", "succeeded",
        ]
        store = StageCheckpointStore(registry)
        assert store.get("pipeline-run:checkpoint-test", 0).status == "succeeded"
        assert store.get("pipeline-run:checkpoint-test", 1).status == "succeeded"


def test_checkpointed_pipeline_stops_on_indeterminate_stage_without_advancing():
    context = {"batch": "safe-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        result = execute_checkpointed_pipeline(
            registry, "pipeline-run:checkpoint-test",
            input_context=context,
            stages=[
                CheckpointedStage(
                    "remote_write",
                    lambda current, key: (_ for _ in ()).throw(RuntimeError("provider timeout")),
                    permission_check=lambda: True,
                    provider_namespace="provider_x",
                ),
                CheckpointedStage(
                    "assemble",
                    lambda current, key: calls.append("must-not-run") or {"ok": True},
                    permission_check=lambda: True,
                    replay_safe=True,
                ),
            ],
            actor="test_worker", reason="checkpointed_pending_test",
        )
        assert result["status"] == "indeterminate"
        assert result["checkpoint_status"] == "indeterminate"
        assert calls == []
        assert registry.get_pipeline_run("pipeline-run:checkpoint-test")["record"]["status"] == "running"
        assert StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 1) is None


def test_checkpointed_pipeline_reconstructs_completed_stage_before_next_stage():
    context = {"batch": "safe-fixture"}
    first_context = {"input": context, "outputs": {}}
    first_output = {"collected": 2}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "collect", first_context,
            lambda current, key: first_output,
            actor="test_worker", reason="seed_completed_stage",
            permission_check=lambda: True, replay_safe=True,
        )
        result = execute_checkpointed_pipeline(
            registry, "pipeline-run:checkpoint-test",
            input_context=context,
            stages=[
                CheckpointedStage(
                    "collect", lambda current, key: {"duplicate": True},
                    permission_check=lambda: True, replay_safe=True,
                    reconstruct=lambda checkpoint: first_output,
                ),
                CheckpointedStage(
                    "assemble",
                    lambda current, key: {"count": current["outputs"]["collect"]["collected"]},
                    permission_check=lambda: True, replay_safe=True,
                ),
            ],
            actor="test_worker", reason="resume_checkpointed_pipeline",
        )
        assert result["status"] == "succeeded"
        assert result["outputs"]["collect"] == first_output
        assert result["outputs"]["assemble"] == {"count": 2}


def test_idempotency_key_is_deterministic_and_bound_to_stage_identity():
    fingerprint = "sha256:" + "a" * 64
    first = stage_idempotency_key("run:1", 0, "collect", fingerprint)
    assert first == stage_idempotency_key("run:1", 0, "collect", fingerprint)
    assert first != stage_idempotency_key("run:1", 1, "collect", fingerprint)
    assert first != stage_idempotency_key("run:1", 0, "normalize", fingerprint)
    assert first.startswith("sha256:")


def test_checkpoint_is_created_before_handler_and_only_hashes_are_persisted():
    secret = "private-article-body-do-not-store"
    context = {"article_text": secret}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        result = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "transform", context,
            lambda current, key: {"summary": secret, "key": key},
            actor="test_worker", reason="test_fresh_execution",
            permission_check=lambda: True, replay_safe=True,
        )
        checkpoint = StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0)
        assert result["status"] == "succeeded"
        assert checkpoint.status == "succeeded"
        assert checkpoint.output_fingerprint == result["output_fingerprint"]
        assert secret.encode() not in registry._connection.serialize()
        assert [item["status"] for item in StageCheckpointStore(registry).history(
            "pipeline-run:checkpoint-test", 0
        )] == ["executing", "succeeded"]


def test_stage_order_requires_every_predecessor_to_be_durably_succeeded():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        with pytest.raises(StageCheckpointError, match="preceding stage checkpoints"):
            store.begin(
                "pipeline-run:checkpoint-test", 1, "normalize",
                sha256_fingerprint({"batch": "stage-one"}),
                provider_namespace=None, actor="test_worker", reason="out_of_order_stage",
            )


def test_succeeded_checkpoint_requires_verified_result_reconstruction():
    context = {"batch": "reconstruction-fixture"}
    output = {"normalized_count": 3}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        first = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "normalize", context,
            lambda current, key: calls.append("executed") or output,
            actor="test_worker", reason="initial_stage_execution",
            permission_check=lambda: True, replay_safe=True,
        )
        assert first["output"] == output
        with pytest.raises(StageCheckpointError, match="reconstruction callback"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "normalize", context,
                lambda current, key: calls.append("must_not_execute"),
                actor="test_worker", reason="resume_without_reconstruction",
                permission_check=lambda: True, replay_safe=True,
            )
        resumed = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "normalize", context,
            lambda current, key: calls.append("must_not_execute"),
            actor="test_worker", reason="resume_with_reconstruction",
            permission_check=lambda: True, replay_safe=True,
            reconstruct=lambda checkpoint: output,
        )
        assert resumed["reused"] is True
        assert resumed["output"] == output
        assert calls == ["executed"]


def test_reconstructed_output_must_match_persisted_fingerprint():
    context = {"batch": "bad-reconstruction-fixture"}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "normalize", context,
            lambda current, key: {"correct": True},
            actor="test_worker", reason="initial_stage_execution",
            permission_check=lambda: True, replay_safe=True,
        )
        with pytest.raises(StageCheckpointError, match="fingerprint does not match"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "normalize", context,
                lambda current, key: {"incorrect": True},
                actor="test_worker", reason="resume_bad_reconstruction",
                permission_check=lambda: True, replay_safe=True,
                reconstruct=lambda checkpoint: {"incorrect": True},
            )


def test_existing_executing_checkpoint_reconciles_not_started_before_retry():
    context = {"batch": "retry-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        first = store.begin(
            "pipeline-run:checkpoint-test", 0, "publish_to_provider",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="initial_intent",
        )
        result = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "publish_to_provider", context,
            lambda current, key: calls.append(key) or {"accepted": True},
            actor="test_worker", reason="recover_after_crash",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=lambda key, ref: Reconciliation("not_started"),
        )
        assert calls == [first.idempotency_key]
        assert result["status"] == "succeeded"
        assert store.get("pipeline-run:checkpoint-test", 0).revision == 2


def test_provider_completed_recovery_never_repeats_handler():
    context = {"batch": "completed-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        store.begin(
            "pipeline-run:checkpoint-test", 0, "send_notice",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="initial_intent",
        )
        result = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "send_notice", context,
            lambda current, key: calls.append("called"),
            actor="test_worker", reason="recover_completed",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=lambda key, ref: Reconciliation(
                "completed", output={"provider_receipt": "receipt_123"},
                operation_ref="op_123",
            ),
        )
        assert calls == []
        assert result["reused"] is True
        checkpoint = store.get("pipeline-run:checkpoint-test", 0)
        assert checkpoint.status == "succeeded"
        assert checkpoint.provider_operation_ref == "op_123"


@pytest.mark.parametrize("outcome,expected", [
    ("pending", "indeterminate"),
    ("unknown", "indeterminate"),
    ("denied", "blocked"),
])
def test_ambiguous_or_denied_provider_outcomes_never_replay(outcome, expected):
    context = {"batch": "uncertain-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        store.begin(
            "pipeline-run:checkpoint-test", 0, "external_action",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="initial_intent",
        )
        with pytest.raises(StageCheckpointError):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "external_action", context,
                lambda current, key: calls.append(key),
                actor="test_worker", reason="recover_uncertain",
                permission_check=lambda: True, provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation(outcome),
            )
        assert calls == []
        assert store.get("pipeline-run:checkpoint-test", 0).status == expected


def test_indeterminate_checkpoint_can_later_reconcile_to_completed_without_replay():
    context = {"batch": "later-completed-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        StageCheckpointStore(registry).begin(
            "pipeline-run:checkpoint-test", 0, "remote_write",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="crash_before_reconciliation",
        )
        with pytest.raises(StageCheckpointError):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
                lambda current, key: calls.append("duplicate"),
                actor="test_worker", reason="first_unknown_result",
                permission_check=lambda: True, provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation("unknown"),
            )
        result = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
            lambda current, key: calls.append("must_not_execute"),
            actor="test_worker", reason="later_completed_result",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=lambda key, ref: Reconciliation(
                "completed", output={"receipt": "confirmed"},
                operation_ref="provider_op_1",
            ),
        )
        assert result["status"] == "succeeded"
        assert result["output"] == {"receipt": "confirmed"}
        assert calls == []
        assert StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0).status == "succeeded"


def test_indeterminate_checkpoint_reexecutes_only_after_later_not_started_proof():
    context = {"batch": "later-not-started-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        StageCheckpointStore(registry).begin(
            "pipeline-run:checkpoint-test", 0, "remote_write",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="crash_before_reconciliation",
        )
        with pytest.raises(StageCheckpointError):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
                lambda current, key: calls.append("must-not-run"),
                actor="test_worker", reason="first_unknown_result",
                permission_check=lambda: True, provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation("unknown"),
            )
        result = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
            lambda current, key: calls.append(key) or {"accepted": True},
            actor="test_worker", reason="later_not_started_result",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=lambda key, ref: Reconciliation("not_started"),
        )
        assert result["status"] == "succeeded"
        assert calls == [result["idempotency_key"]]
        history = StageCheckpointStore(registry).history("pipeline-run:checkpoint-test", 0)
        assert [item["status"] for item in history] == [
            "executing", "indeterminate", "executing", "succeeded",
        ]


def test_permission_is_rechecked_before_execution_and_denial_blocks():
    context = {"batch": "permission-fixture"}
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        with pytest.raises(StageCheckpointError, match="permission check denied"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "collect", context,
                lambda current, key: calls.append(key),
                actor="test_worker", reason="permission_revoked",
                permission_check=lambda: False, replay_safe=True,
            )
        assert calls == []
        assert StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0).status == "blocked"


def test_handler_exception_is_indeterminate_for_non_replay_safe_stage():
    context = {"batch": "exception-fixture"}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        with pytest.raises(StageCheckpointError, match="exception details were not persisted"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
                lambda current, key: (_ for _ in ()).throw(RuntimeError("secret payload")),
                actor="test_worker", reason="test_handler_error",
                permission_check=lambda: True, provider_namespace="provider_x",
            )
        checkpoint = StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0)
        assert checkpoint.status == "indeterminate"
        assert b"secret payload" not in registry._connection.serialize()


def test_checkpoint_identity_and_revision_guards_reject_tampering():
    context = {"batch": "guard-fixture"}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        checkpoint = store.begin(
            "pipeline-run:checkpoint-test", 0, "collect",
            sha256_fingerprint(context), provider_namespace=None,
            actor="test_worker", reason="initial_intent",
        )
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            registry._connection.execute(
                """UPDATE pipeline_stage_checkpoints
                   SET stage_name = 'tampered', revision = 2 WHERE pipeline_run_id = ? AND stage_ordinal = 0""",
                ("pipeline-run:checkpoint-test",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                """UPDATE pipeline_stage_checkpoint_revisions SET reason = 'tampered'
                   WHERE pipeline_run_id = ? AND stage_ordinal = 0 AND revision = 1""",
                ("pipeline-run:checkpoint-test",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="metadata contains unsupported"):
            registry._connection.execute(
                """UPDATE pipeline_stage_checkpoints
                   SET metadata_json = ?, revision = 2
                   WHERE pipeline_run_id = ? AND stage_ordinal = 0""",
                ('{"article_text":"must-not-persist"}', "pipeline-run:checkpoint-test"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="metadata contains unsupported"):
            registry._connection.execute(
                """UPDATE pipeline_stage_checkpoints
                   SET metadata_json = ?, revision = 2
                   WHERE pipeline_run_id = ? AND stage_ordinal = 0""",
                ('{"provider_state":"private article text"}', "pipeline-run:checkpoint-test"),
            )
        changed = store.transition(
            checkpoint, "indeterminate", actor="test_worker", reason="first_transition",
            provider_operation_ref="provider_op_1",
        )
        with pytest.raises(sqlite3.IntegrityError, match="reference is immutable"):
            store.transition(
                changed, "blocked", actor="test_worker", reason="provider_ref_tamper",
                provider_operation_ref="provider_op_2",
            )
        with pytest.raises(StageCheckpointError, match="revision conflict"):
            store.transition(
                checkpoint, "blocked", actor="test_worker", reason="stale_transition",
            )
        assert changed.revision == 2


def test_checkpoint_rejects_free_form_metadata_that_could_leak_content():
    context = {"batch": "metadata-fixture"}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        checkpoint = store.begin(
            "pipeline-run:checkpoint-test", 0, "collect",
            sha256_fingerprint(context), provider_namespace=None,
            actor="test_worker", reason="initial_intent",
        )
        with pytest.raises(ValueError, match="unsupported fields"):
            store.transition(
                checkpoint, "failed", actor="test_worker", reason="safe_failure",
                metadata={"article_text": "must-not-persist"},
            )


def test_external_success_before_local_commit_is_reconciled_without_duplicate(monkeypatch):
    context = {"batch": "commit-gap-fixture"}
    side_effects = []
    original_transition = StageCheckpointStore.transition

    with SQLiteRegistry() as registry:
        _running_run(registry)

        def fail_local_commit(self, checkpoint, status, **kwargs):
            raise OSError("simulated local commit failure")

        monkeypatch.setattr(StageCheckpointStore, "transition", fail_local_commit)
        with pytest.raises(OSError, match="simulated local commit failure"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
                lambda current, key: side_effects.append(key) or {"receipt": "remote_receipt_1"},
                actor="test_worker", reason="simulate_commit_gap",
                permission_check=lambda: True, provider_namespace="provider_x",
            )
        assert len(side_effects) == 1
        assert StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0).status == "executing"

        monkeypatch.setattr(StageCheckpointStore, "transition", original_transition)
        recovered = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "remote_write", context,
            lambda current, key: side_effects.append("duplicate") or {"receipt": "duplicate"},
            actor="test_worker", reason="reconcile_commit_gap",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=lambda key, ref: Reconciliation(
                "completed", output={"receipt": "remote_receipt_1"},
                operation_ref="remote_op_1",
            ),
        )
        assert recovered["output"] == {"receipt": "remote_receipt_1"}
        assert recovered["reused"] is True
        assert len(side_effects) == 1
        assert StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0).status == "succeeded"


def test_checkpoint_transition_and_audit_event_roll_back_atomically(monkeypatch):
    context = {"batch": "atomic-rollback-fixture"}
    with SQLiteRegistry() as registry:
        _running_run(registry)
        store = StageCheckpointStore(registry)
        checkpoint = store.begin(
            "pipeline-run:checkpoint-test", 0, "transform",
            sha256_fingerprint(context), provider_namespace=None,
            actor="test_worker", reason="initial_intent",
        )

        def fail_audit(*args, **kwargs):
            raise OSError("simulated audit insert failure")

        monkeypatch.setattr(registry, "_append_audit", fail_audit)
        with pytest.raises(OSError, match="simulated audit insert failure"):
            store.transition(
                checkpoint, "succeeded", actor="test_worker", reason="atomic_success",
                output_fingerprint=sha256_fingerprint({"ok": True}),
            )
        monkeypatch.undo()
        current = store.get("pipeline-run:checkpoint-test", 0)
        assert current.status == "executing"
        assert current.revision == 1
        assert [item["revision"] for item in store.history("pipeline-run:checkpoint-test", 0)] == [1]


def test_two_registry_connections_share_one_durable_checkpoint(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    database = tmp_path / "shared-checkpoint.sqlite"
    context = {"batch": "concurrent-checkpoint"}
    with SQLiteRegistry(database) as left, SQLiteRegistry(database) as right:
        _running_run(left)
        def create(store, actor):
            return store.begin(
                "pipeline-run:checkpoint-test", 0, "collect",
                sha256_fingerprint(context), provider_namespace=None,
                actor=actor, reason="concurrent_intent",
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create, StageCheckpointStore(left), "worker_one")
            second = pool.submit(create, StageCheckpointStore(right), "worker_two")
            a, b = first.result(timeout=10), second.result(timeout=10)
        assert a.idempotency_key == b.idempotency_key
        assert {a.created, b.created} == {True, False}
        assert StageCheckpointStore(left).get("pipeline-run:checkpoint-test", 0).revision == 1


def test_schema_31_migrates_to_32_with_checkpoint_tables(tmp_path, monkeypatch):
    from sngso_engine import registry as registry_module

    database = tmp_path / "schema-31.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 31)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 31
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 32)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 32
        tables = {
            row[0] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "pipeline_stage_checkpoints" in tables
        assert "pipeline_stage_checkpoint_revisions" in tables

def test_pending_reconciliation_persists_operation_reference_for_later_lookup():
    with SQLiteRegistry() as registry:
        _running_run(registry)
        context = {"payload": {"safe": True}}
        StageCheckpointStore(registry).begin(
            "pipeline-run:checkpoint-test", 0, "remote_write",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="seed_pending_checkpoint",
        )
        with pytest.raises(StageCheckpointError, match="remains pending"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "remote_write",
                context,
                lambda current, key: pytest.fail("pending operation must never replay"),
                actor="test_worker", reason="pending_provider_reconcile",
                permission_check=lambda: True, provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation(
                    "pending", operation_ref="provider-op-7",
                    metadata={"poll_after": "later"},
                ),
                replay_safe=False,
            )
        checkpoint = StageCheckpointStore(registry).get("pipeline-run:checkpoint-test", 0)
        assert checkpoint.status == "indeterminate"
        assert checkpoint.provider_operation_ref == "provider-op-7"
        assert checkpoint.metadata == {
            "provider_state": "pending", "reconciliation": "pending",
        }
        history = StageCheckpointStore(registry).history("pipeline-run:checkpoint-test", 0)
        assert history[-1]["provider_operation_ref"] == "provider-op-7"
        assert history[-1]["metadata"]["reconciliation"] == "pending"


def test_pending_reconciliation_can_be_polled_again_using_persisted_operation_reference():
    calls = []
    with SQLiteRegistry() as registry:
        _running_run(registry)
        context = {"payload": {"safe": True}}
        StageCheckpointStore(registry).begin(
            "pipeline-run:checkpoint-test", 0, "remote_write",
            sha256_fingerprint(context), provider_namespace="provider_x",
            actor="test_worker", reason="seed_pending_checkpoint",
        )
        with pytest.raises(StageCheckpointError, match="remains pending"):
            execute_checkpointed_stage(
                registry, "pipeline-run:checkpoint-test", 0, "remote_write",
                context,
                lambda current, key: pytest.fail("pending operation must never replay"),
                actor="test_worker", reason="first_pending_poll",
                permission_check=lambda: True, provider_namespace="provider_x",
                reconcile=lambda key, ref: Reconciliation(
                    "pending", operation_ref="provider-op-8",
                ),
                replay_safe=False,
            )
        def reconcile_again(key, ref):
            calls.append((key, ref))
            return Reconciliation("completed", output={"remote_id": "done"}, operation_ref=ref)
        result = execute_checkpointed_stage(
            registry, "pipeline-run:checkpoint-test", 0, "remote_write",
            context,
            lambda current, key: pytest.fail("confirmed completion must not replay"),
            actor="test_worker", reason="second_pending_poll",
            permission_check=lambda: True, provider_namespace="provider_x",
            reconcile=reconcile_again, replay_safe=False,
        )
        assert result["status"] == "succeeded"
        assert result["output"] == {"remote_id": "done"}
        assert calls == [(result["idempotency_key"], "provider-op-8")]



def test_restart_recovery_inventory_lists_unresolved_checkpoints_without_mutating_them():
    with SQLiteRegistry() as registry:
        _running_run(registry, run_id="pipeline-run:restart-a")
        _running_run(registry, run_id="pipeline-run:restart-b")
        store = StageCheckpointStore(registry)
        indeterminate = store.begin(
            "pipeline-run:restart-a", 0, "remote_write",
            sha256_fingerprint({"payload": "a"}),
            provider_namespace="provider_x", actor="test_worker",
            reason="seed_interrupted_checkpoint",
        )
        store.transition(
            indeterminate, "indeterminate", actor="test_worker",
            reason="seed_provider_pending",
            provider_operation_ref="provider-operation-a",
            metadata={"reconciliation": "pending", "provider_state": "pending"},
        )
        executing = store.begin(
            "pipeline-run:restart-b", 0, "local_transform",
            sha256_fingerprint({"payload": "b"}),
            provider_namespace=None, actor="test_worker",
            reason="seed_executing_checkpoint",
        )

        candidates = store.list_unresolved()
        assert [(item.pipeline_run_id, item.status) for item in candidates] == [
            ("pipeline-run:restart-a", "indeterminate"),
            ("pipeline-run:restart-b", "executing"),
        ]
        assert candidates[0].provider_operation_ref == "provider-operation-a"
        assert candidates[0].idempotency_key
        assert store.get("pipeline-run:restart-a", 0).revision == candidates[0].revision
        assert store.get("pipeline-run:restart-b", 0).revision == executing.revision

        scoped = store.list_unresolved("pipeline-run:restart-a")
        assert len(scoped) == 1
        assert scoped[0].pipeline_run_id == "pipeline-run:restart-a"
        assert store.list_unresolved("pipeline-run:missing") == []


def test_restart_recovery_inventory_rejects_empty_run_filter():
    with SQLiteRegistry() as registry:
        store = StageCheckpointStore(registry)
        with pytest.raises(ValueError, match="non-empty"):
            store.list_unresolved("")
