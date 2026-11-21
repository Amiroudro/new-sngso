from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.operations import create_pipeline_run_idempotently
from sngso_engine.orchestrator import (
    PipelineExecutionError,
    PipelineStage,
    PipelineStageFailure,
    execute_pipeline_run,
)
from sngso_engine.registry import SQLiteRegistry


def _queued_run(registry, input_context, *, run_id=None):
    payload = {
        "schema_version": "1.0.0",
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "engine-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": [sha256_fingerprint(input_context)],
        "initiated_by": "test-scheduler",
    }
    return create_pipeline_run_idempotently(
        registry,
        dict(payload, **({"id": run_id} if run_id else {})),
        run_kind="test-pipeline",
        actor="test-scheduler",
        reason="Create queued test run.",
    )


def test_pipeline_runner_executes_stages_in_order_and_persists_hashes():
    context = {"edition_date": "2026-11-22", "source_batch": ["batch-1"]}
    observed = []

    def collect(stage_context):
        observed.append(("collect", stage_context["input"]["edition_date"]))
        return {"article_ids": ["article-1"], "count": 1}

    def normalize(stage_context):
        observed.append(("normalize", list(stage_context["outputs"])))
        assert stage_context["outputs"]["collect"]["count"] == 1
        return {"normalized_count": 1}

    with SQLiteRegistry() as registry:
        created = _queued_run(registry, context)
        result = execute_pipeline_run(
            registry,
            created["record"]["id"],
            input_context=context,
            stages=[
                PipelineStage("collect", collect),
                PipelineStage("normalize", normalize),
            ],
            actor="test-worker",
            reason="Execute deterministic fixture stages.",
        )
        stored = registry.get_pipeline_run(created["record"]["id"])
        assert result["status"] == "succeeded"
        assert observed == [("collect", "2026-11-22"), ("normalize", ["collect"])]
        assert stored["record"]["status"] == "succeeded"
        assert len(stored["record"]["stage_results"]) == 2
        assert all(item["status"] == "succeeded" for item in stored["record"]["stage_results"])
        assert len(stored["record"]["output_hashes"]) == 2
        assert stored["revision"] == 3
        assert [item["record"]["status"] for item in registry.pipeline_run_history(created["record"]["id"])] == [
            "queued", "running", "succeeded",
        ]


def test_pipeline_runner_stops_after_failure_and_blocks_remaining_stages():
    context = {"batch": "safe-fixture"}
    calls = []

    def fail(_):
        calls.append("permission-check")
        raise PipelineStageFailure("permission_revoked", "Required permission was revoked.")

    def should_not_run(_):
        calls.append("collect")
        return {"unexpected": True}

    with SQLiteRegistry() as registry:
        created = _queued_run(registry, context)
        result = execute_pipeline_run(
            registry,
            created["record"]["id"],
            input_context=context,
            stages=[
                PipelineStage("permission-check", fail),
                PipelineStage("collect", should_not_run),
            ],
            actor="test-worker",
            reason="Test fail-closed execution.",
        )
        stored = registry.get_pipeline_run(created["record"]["id"])["record"]
        assert result["status"] == "failed"
        assert calls == ["permission-check"]
        assert stored["status"] == "failed"
        assert stored["failure_code"] == "permission_revoked"
        assert [item["status"] for item in stored["stage_results"]] == ["failed", "blocked"]
        assert result["retry_decision"]["retryable"] is False
        assert result["retry_decision"]["category"] == "permanent"


def test_pipeline_runner_does_not_leak_untrusted_exception_message():
    context = {"batch": "safe-fixture"}

    def broken(_):
        raise RuntimeError("secret-token=do-not-log article-body=private")

    with SQLiteRegistry() as registry:
        created = _queued_run(registry, context)
        result = execute_pipeline_run(
            registry,
            created["record"]["id"],
            input_context=context,
            stages=[PipelineStage("transform", broken)],
            actor="test-worker",
            reason="Test safe error recording.",
        )
        stored = registry.get_pipeline_run(created["record"]["id"])["record"]
        assert result["status"] == "failed"
        assert stored["failure_code"] == "stage_execution_failed"
        assert "secret-token" not in stored["failure_detail"]
        assert "article-body" not in stored["failure_detail"]


def test_transient_stage_failure_is_classified_but_never_retried_automatically():
    context = {"batch": "safe-fixture"}
    calls = []

    def flaky(_):
        calls.append("called")
        raise PipelineStageFailure("network_timeout", "Injected stage timed out.")

    with SQLiteRegistry() as registry:
        created = _queued_run(registry, context)
        result = execute_pipeline_run(
            registry,
            created["record"]["id"],
            input_context=context,
            stages=[PipelineStage("fetch", flaky)],
            actor="test-worker",
            reason="Test no automatic retry.",
        )
        assert calls == ["called"]
        assert result["retry_decision"]["retryable"] is True
        assert result["status"] == "failed"


@pytest.mark.parametrize(
    "mutations",
    [
        {"input_context": {"different": "input"}},
        {"stages": []},
        {"stages": [PipelineStage("duplicate", lambda _: {}), PipelineStage("duplicate", lambda _: {})]},
    ],
)
def test_pipeline_runner_rejects_invalid_claim_before_executing(mutations):
    context = {"batch": "safe-fixture"}
    calls = []
    stages = [PipelineStage("stage", lambda _: calls.append("ran") or {})]
    with SQLiteRegistry() as registry:
        created = _queued_run(registry, context)
        input_context = mutations.get("input_context", context)
        selected_stages = mutations.get("stages", stages)
        with pytest.raises(PipelineExecutionError):
            execute_pipeline_run(
                registry,
                created["record"]["id"],
                input_context=input_context,
                stages=selected_stages,
                actor="test-worker",
                reason="Test preflight rejection.",
            )
        assert calls == []
        assert registry.get_pipeline_run(created["record"]["id"])["record"]["status"] == "queued"


def test_pipeline_runner_rejects_running_or_terminal_runs_without_reexecution():
    context = {"batch": "safe-fixture"}
    calls = []
    stage = PipelineStage("stage", lambda _: calls.append("ran") or {"ok": True})
    with SQLiteRegistry() as registry:
        created = _queued_run(registry, context)
        run_id = created["record"]["id"]
        execute_pipeline_run(
            registry, run_id, input_context=context, stages=[stage],
            actor="test-worker", reason="First execution.",
        )
        with pytest.raises(PipelineExecutionError, match="only a queued"):
            execute_pipeline_run(
                registry, run_id, input_context=context, stages=[stage],
                actor="test-worker", reason="Duplicate execution.",
            )
        assert calls == ["ran"]


def test_pipeline_stage_failure_rejects_unsafe_failure_metadata():
    with pytest.raises(ValueError):
        PipelineStageFailure("Invalid Code", "safe")
    with pytest.raises(ValueError):
        PipelineStageFailure("network_timeout", "x" * 2001)


def test_two_workers_cannot_execute_the_same_queued_run_twice(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    database = tmp_path / "worker-claim.sqlite"
    context = {"batch": "single-claim"}
    entered = Event()
    release = Event()
    with SQLiteRegistry(database) as left, SQLiteRegistry(database) as right:
        created = _queued_run(left, context)
        run_id = created["record"]["id"]

        def slow_stage(_):
            entered.set()
            assert release.wait(timeout=5)
            return {"finished": True}

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                execute_pipeline_run,
                left,
                run_id,
                input_context=context,
                stages=[PipelineStage("work", slow_stage)],
                actor="worker-one",
                reason="Claim run on first worker.",
            )
            assert entered.wait(timeout=5)
            with pytest.raises(PipelineExecutionError, match="only a queued"):
                execute_pipeline_run(
                    right,
                    run_id,
                    input_context=context,
                    stages=[PipelineStage("work", lambda _: {"duplicate": True})],
                    actor="worker-two",
                    reason="Attempt duplicate claim.",
                )
            release.set()
            result = first.result(timeout=5)

        assert result["status"] == "succeeded"
        assert "outputs" not in result
        history = left.pipeline_run_history(run_id)
        assert [item["record"]["status"] for item in history] == ["queued", "running", "succeeded"]

