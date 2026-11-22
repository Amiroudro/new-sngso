from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.operations import create_pipeline_run_idempotently
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.stage_checkpoints import (
    Reconciliation,
    StageCheckpointError,
    StageCheckpointStore,
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
    ("pending", "executing"),
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
        with pytest.raises(StageCheckpointError, match="revision conflict"):
            store.transition(
                checkpoint, "succeeded", actor="test_worker", reason="stale_transition",
                output_fingerprint="sha256:" + "b" * 64,
            )


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
