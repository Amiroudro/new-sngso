from __future__ import annotations

import json
import sqlite3

import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry, RegistryMigrationError
from sngso_engine import registry as registry_module


STAMP = "2026-11-21T05:00:00Z"


def run_record(status: str = "running") -> dict:
    payload = {
        "schema_version": "1.0.0",
        "id": "pipeline-run:daily-2026-11-21",
        "status": status,
        "started_at": STAMP,
        "software_version": "2.0.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": ["sha256:" + "b" * 64],
        "initiated_by": "scheduler:daily",
        "stage_results": [{"stage": "collect", "status": "succeeded"}],
    }
    return payload


def test_pipeline_run_is_persisted_with_append_only_revision_history() -> None:
    with SQLiteRegistry() as registry:
        created = registry.add_pipeline_run(run_record(), actor="scheduler", reason="daily run started")
        assert created["revision"] == 1
        current = registry.get_pipeline_run("pipeline-run:daily-2026-11-21")
        assert current == created
        history = registry.pipeline_run_history("pipeline-run:daily-2026-11-21")
        assert len(history) == 1
        assert history[0]["record"]["status"] == "running"
        assert history[0]["actor"] == "scheduler"


def test_pipeline_run_can_transition_to_succeeded_and_is_resolvable() -> None:
    with SQLiteRegistry() as registry:
        registry.add_pipeline_run(run_record(), actor="scheduler", reason="start")
        completed = run_record("succeeded")
        completed.update(
            finished_at="2026-11-21T05:10:00Z",
            output_hashes=["sha256:" + "c" * 64],
            stage_results=[
                {"stage": "collect", "status": "succeeded"},
                {"stage": "assemble", "status": "succeeded"},
            ],
        )
        result = registry.transition_pipeline_run(
            "pipeline-run:daily-2026-11-21", completed,
            expected_revision=1, actor="worker", reason="all stages completed",
        )
        assert result["revision"] == 2
        assert result["record"]["status"] == "succeeded"
        assert registry.get_pipeline_run("pipeline-run:daily-2026-11-21") == result
        assert [item["revision"] for item in registry.pipeline_run_history("pipeline-run:daily-2026-11-21")] == [1, 2]


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "rejected"])
def test_new_pipeline_run_cannot_be_created_in_terminal_state(status: str) -> None:
    with SQLiteRegistry() as registry:
        payload = run_record(status)
        if status == "succeeded":
            payload.update(finished_at="2026-11-21T05:10:00Z", output_hashes=["sha256:" + "c" * 64])
        elif status in {"failed", "rejected"}:
            payload.update(finished_at="2026-11-21T05:10:00Z", failure_code="failed")
        else:
            payload["finished_at"] = "2026-11-21T05:10:00Z"
        with pytest.raises(RegistryIntegrityError, match="start queued or running"):
            registry.add_pipeline_run(payload, actor="scheduler", reason="invalid terminal insertion")


def test_pipeline_run_rejects_illegal_transition_and_immutable_input_change() -> None:
    with SQLiteRegistry() as registry:
        registry.add_pipeline_run(run_record(), actor="scheduler", reason="start")
        invalid = run_record("succeeded")
        invalid.update(finished_at="2026-11-21T05:10:00Z", output_hashes=["sha256:" + "c" * 64])
        invalid["config_hash"] = "sha256:" + "d" * 64
        with pytest.raises(RegistryIntegrityError, match="identity and inputs are immutable"):
            registry.transition_pipeline_run(
                "pipeline-run:daily-2026-11-21", invalid,
                expected_revision=1, actor="worker", reason="tamper attempt",
            )
        with pytest.raises(RegistryIntegrityError, match="revision conflict"):
            registry.transition_pipeline_run(
                "pipeline-run:daily-2026-11-21", run_record("cancelled") | {"finished_at": "2026-11-21T05:10:00Z"},
                expected_revision=2, actor="worker", reason="stale update",
            )


def test_pipeline_run_unknown_id_and_schema_26_migrates_to_27(tmp_path, monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        assert registry.get_pipeline_run("pipeline-run:missing") is None
        assert registry.pipeline_run_history("pipeline-run:missing") == []

    database = tmp_path / "pipeline-run-v26.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 26)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 26
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 27)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 27


def test_sqlite_guards_reject_illegal_direct_transition_and_deletion() -> None:
    with SQLiteRegistry() as registry:
        queued = run_record("queued")
        registry.add_pipeline_run(queued, actor="scheduler", reason="queued")
        forged_success = dict(queued)
        forged_success.update(
            status="succeeded",
            finished_at="2026-11-21T05:10:00Z",
            output_hashes=["sha256:" + "c" * 64],
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid pipeline run status transition"):
            registry._connection.execute(
                """UPDATE pipeline_runs SET status = ?, revision = 2, payload_json = ?
                   WHERE id = ?""",
                ("succeeded", json.dumps(forged_success), queued["id"]),
            )
        with pytest.raises(sqlite3.IntegrityError, match="pipeline run records cannot be deleted"):
            registry._connection.execute(
                "DELETE FROM pipeline_runs WHERE id = ?", (queued["id"],)
            )


def _failed_parent(registry):
    parent = run_record("running")
    registry.add_pipeline_run(parent, actor="scheduler", reason="create retry parent")
    failed = dict(
        parent,
        status="failed",
        finished_at="2026-11-21T05:10:00Z",
        failure_code="network_timeout",
    )
    registry.transition_pipeline_run(
        parent["id"], failed, expected_revision=1,
        actor="worker", reason="parent attempt failed",
    )
    return parent


def test_pipeline_retry_lineage_requires_failed_parent_and_matching_inputs():
    with SQLiteRegistry() as registry:
        parent = _failed_parent(registry)
        retry = dict(
            run_record("queued"),
            id="pipeline-run:retry-2026-11-21",
            attempt=2,
            retry_of=parent["id"],
        )
        created = registry.add_pipeline_run(
            retry, actor="operator", reason="create explicit retry child",
        )
        assert created["record"]["retry_of"] == parent["id"]
        assert created["record"]["attempt"] == 2


def test_pipeline_retry_lineage_rejects_missing_or_nonfailed_parent():
    with SQLiteRegistry() as registry:
        child = dict(
            run_record("queued"),
            id="pipeline-run:retry-missing-parent",
            attempt=2,
            retry_of="pipeline-run:missing-parent",
        )
        with pytest.raises(RegistryIntegrityError, match="does not exist"):
            registry.add_pipeline_run(child, actor="operator", reason="invalid retry parent")

        parent = run_record("running")
        registry.add_pipeline_run(parent, actor="scheduler", reason="create active parent")
        child["id"] = "pipeline-run:retry-active-parent"
        child["retry_of"] = parent["id"]
        with pytest.raises(RegistryIntegrityError, match="must be failed"):
            registry.add_pipeline_run(child, actor="operator", reason="retry active parent")


def test_pipeline_retry_lineage_rejects_changed_attempt_or_parent_inputs():
    with SQLiteRegistry() as registry:
        parent = _failed_parent(registry)
        wrong_attempt = dict(
            run_record("queued"),
            id="pipeline-run:retry-wrong-attempt",
            attempt=3,
            retry_of=parent["id"],
        )
        with pytest.raises(RegistryIntegrityError, match="immediately follow"):
            registry.add_pipeline_run(
                wrong_attempt, actor="operator", reason="wrong retry attempt",
            )

        changed_input = dict(
            run_record("queued"),
            id="pipeline-run:retry-changed-input",
            attempt=2,
            retry_of=parent["id"],
            config_hash="sha256:" + "d" * 64,
        )
        with pytest.raises(RegistryIntegrityError, match="preserve its parent's"):
            registry.add_pipeline_run(
                changed_input, actor="operator", reason="changed retry input",
            )

