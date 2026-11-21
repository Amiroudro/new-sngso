from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.operations import OperationalReportError, build_operational_health_report
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.operations import create_pipeline_run_idempotently


def _run(registry, run_id, started_at, *, terminal=None, finished_at=None):
    record = {
        "schema_version": "1.0.0",
        "id": run_id,
        "status": "running",
        "started_at": started_at.isoformat(),
        "software_version": "test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": ["sha256:" + "b" * 64],
        "initiated_by": "test-scheduler",
    }
    registry.add_pipeline_run(record, actor="test", reason="Create operational fixture.")
    if terminal:
        completed = dict(record, status=terminal, finished_at=finished_at.isoformat())
        if terminal == "succeeded":
            completed["output_hashes"] = ["sha256:" + "c" * 64]
        else:
            completed["failure_code"] = "fixture_failure"
            completed["failure_detail"] = "This private detail must not appear in health output."
        registry.transition_pipeline_run(
            run_id, completed, expected_revision=1,
            actor="test-worker", reason="Complete operational fixture.",
        )


def test_list_pipeline_runs_is_bounded_filterable_and_newest_first():
    now = datetime.now(timezone.utc)
    with SQLiteRegistry() as registry:
        _run(registry, "pipeline-run:old", now - timedelta(hours=2), terminal="succeeded",
             finished_at=now - timedelta(hours=1))
        _run(registry, "pipeline-run:new", now - timedelta(minutes=5))
        all_runs = registry.list_pipeline_runs(limit=10)
        assert [item["record"]["id"] for item in all_runs] == [
            "pipeline-run:new", "pipeline-run:old",
        ]
        assert registry.list_pipeline_runs(status="running")[0]["record"]["id"] == "pipeline-run:new"
        assert registry.list_pipeline_runs(status="succeeded")[0]["record"]["id"] == "pipeline-run:old"
        with pytest.raises(ValueError, match="status filter"):
            registry.list_pipeline_runs(status="unknown")
        with pytest.raises(ValueError, match="status filter"):
            registry.list_pipeline_runs(status=["running"])
        with pytest.raises(ValueError, match="limit"):
            registry.list_pipeline_runs(limit=0)


def test_operational_report_flags_stale_runs_recent_failures_and_keeps_details_private():
    now = datetime.now(timezone.utc)
    with SQLiteRegistry() as registry:
        _run(registry, "pipeline-run:stale", now - timedelta(hours=2))
        _run(registry, "pipeline-run:failed", now - timedelta(minutes=20),
             terminal="failed", finished_at=now - timedelta(minutes=10))
        report = build_operational_health_report(
            registry, now=now, stale_after=timedelta(minutes=30),
            recent_failure_window=timedelta(hours=24),
        )
        assert report["status"] == "degraded"
        assert report["production_ready"] is False
        assert report["audit"]["chain_valid"] is True
        assert report["pipeline_runs"]["counts_by_status"]["running"] == 1
        assert report["pipeline_runs"]["counts_by_status"]["failed"] == 1
        assert [item["run_id"] for item in report["pipeline_runs"]["stale_active_runs"]] == [
            "pipeline-run:stale",
        ]
        assert [item["run_id"] for item in report["pipeline_runs"]["recent_failures"]] == [
            "pipeline-run:failed",
        ]
        assert "failure_detail" not in str(report)
        assert "private detail" not in str(report)


def test_operational_report_marks_empty_history_not_observed_not_healthy():
    with SQLiteRegistry() as registry:
        report = build_operational_health_report(registry)
        assert report["status"] == "not_observed"
        assert report["production_ready"] is False
        assert "no_pipeline_runs_observed" in report["warnings"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"now": datetime(2026, 11, 22, 12, 0)},
        {"stale_after": timedelta(0)},
        {"recent_failure_window": timedelta(seconds=-1)},
        {"limit": True},
        {"limit": 5001},
    ],
)
def test_operational_report_rejects_invalid_parameters(kwargs):
    with SQLiteRegistry() as registry:
        with pytest.raises(OperationalReportError):
            build_operational_health_report(registry, **kwargs)


def test_retry_policy_retries_only_recognized_transient_failures_with_bounded_backoff():
    from sngso_engine.operations import classify_retry_policy

    first = classify_retry_policy("network_timeout", attempt=1)
    second = classify_retry_policy("HTTP_503", attempt=2)
    exhausted = classify_retry_policy("sqlite_busy", attempt=3)
    assert first["retryable"] is True
    assert first["delay_seconds"] == 2.0
    assert second["retryable"] is True
    assert second["delay_seconds"] == 4.0
    assert exhausted["retryable"] is False
    assert exhausted["reason"] == "attempt_limit_reached"
    capped = classify_retry_policy(
        "http_429", attempt=9, max_attempts=10,
        base_delay_seconds=5, max_delay_seconds=20,
    )
    assert capped["retryable"] is True
    assert capped["delay_seconds"] == 20.0


@pytest.mark.parametrize(
    "failure_code",
    [
        "permission_denied", "permission_expired", "permission_revoked",
        "identity_revoked", "authentication_failed", "contract_validation_failed",
        "schema_invalid", "content_policy_blocked", "integrity_violation",
        "unknown_vendor_error",
    ],
)
def test_retry_policy_fails_closed_for_permanent_and_unknown_failures(failure_code):
    from sngso_engine.operations import classify_retry_policy

    result = classify_retry_policy(failure_code)
    assert result["retryable"] is False
    assert result["delay_seconds"] == 0
    assert result["category"] in {"permanent", "unknown"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"failure_code": ""},
        {"failure_code": "network_timeout", "attempt": 0},
        {"failure_code": "network_timeout", "attempt": True},
        {"failure_code": "network_timeout", "max_attempts": 0},
        {"failure_code": "network_timeout", "base_delay_seconds": 0},
        {"failure_code": "network_timeout", "base_delay_seconds": 10, "max_delay_seconds": 5},
    ],
)
def test_retry_policy_rejects_invalid_inputs(kwargs):
    from sngso_engine.operations import classify_retry_policy

    failure_code = kwargs.pop("failure_code")
    with pytest.raises(OperationalReportError):
        classify_retry_policy(failure_code, **kwargs)


def _idempotent_run_payload(started_at=None, *, config_hash=None, input_hash=None):
    from datetime import datetime, timezone

    return {
        "schema_version": "1.0.0",
        "status": "running",
        "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
        "software_version": "engine-test/1.0",
        "config_hash": config_hash or "sha256:" + "a" * 64,
        "input_hashes": [input_hash or "sha256:" + "b" * 64],
        "initiated_by": "test-scheduler",
    }


def test_idempotent_pipeline_creation_reuses_existing_logical_run():
    from datetime import datetime, timedelta, timezone
    from sngso_engine.operations import create_pipeline_run_idempotently

    now = datetime.now(timezone.utc)
    with SQLiteRegistry() as registry:
        first = create_pipeline_run_idempotently(
            registry, _idempotent_run_payload(now), run_kind="daily-edition",
            actor="test", reason="Start logical daily run.",
        )
        duplicate = create_pipeline_run_idempotently(
            registry, _idempotent_run_payload(now + timedelta(minutes=1)),
            run_kind="daily-edition", actor="test", reason="Duplicate submission.",
        )
        assert first["created"] is True
        assert duplicate["created"] is False
        assert duplicate["record"]["id"] == first["record"]["id"]
        assert duplicate["record"]["started_at"] == now.isoformat()
        assert len(registry.list_pipeline_runs(limit=10)) == 1


def test_idempotency_key_changes_for_new_inputs_or_retry_attempt():
    from sngso_engine.operations import create_pipeline_run_idempotently

    with SQLiteRegistry() as registry:
        first = create_pipeline_run_idempotently(
            registry, _idempotent_run_payload(), run_kind="daily-edition",
            actor="test", reason="First attempt.",
        )
        changed_input = create_pipeline_run_idempotently(
            registry, _idempotent_run_payload(input_hash="sha256:" + "c" * 64),
            run_kind="daily-edition", actor="test", reason="Changed input.",
        )
        retry = create_pipeline_run_idempotently(
            registry, _idempotent_run_payload(), run_kind="daily-edition",
            actor="test", reason="Explicit retry attempt.", attempt=2,
        )
        assert len({first["record"]["id"], changed_input["record"]["id"], retry["record"]["id"]}) == 3
        assert len(registry.list_pipeline_runs(limit=10)) == 3


def test_concurrent_duplicate_pipeline_submissions_converge_on_one_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sngso_engine.operations import create_pipeline_run_idempotently

    database = tmp_path / "idempotency.sqlite"
    payload = _idempotent_run_payload()
    with SQLiteRegistry(database) as left, SQLiteRegistry(database) as right:
        barrier = Barrier(2)

        def submit(registry):
            barrier.wait(timeout=5)
            return create_pipeline_run_idempotently(
                registry, dict(payload), run_kind="daily-edition",
                actor="test", reason="Concurrent duplicate submission.",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (left, right)))
        assert sum(result["created"] for result in results) == 1
        assert len({result["record"]["id"] for result in results}) == 1
        assert len(left.list_pipeline_runs(limit=10)) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"run_kind": ""},
        {"input_hashes": None},
        {"input_hashes": [{"not": "a hash"}]},
        {"input_hashes": ["sha256:" + "a" * 64, "sha256:" + "a" * 64]},
        {"config_hash": "not-a-hash"},
        {"attempt": 0},
    ],
)
def test_pipeline_idempotency_key_rejects_invalid_inputs(kwargs):
    from sngso_engine.operations import pipeline_run_idempotency_key

    arguments = {
        "run_kind": "daily-edition",
        "software_version": "engine-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": ["sha256:" + "b" * 64],
        "initiated_by": "test-scheduler",
        "attempt": 1,
    }
    arguments.update(kwargs)
    with pytest.raises(OperationalReportError):
        pipeline_run_idempotency_key(**arguments)


def test_pipeline_idempotency_key_preserves_declared_input_order():
    from sngso_engine.operations import pipeline_run_idempotency_key

    common = {
        "run_kind": "multi-stage-run",
        "software_version": "engine-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "initiated_by": "test-scheduler",
    }
    first = pipeline_run_idempotency_key(
        **common, input_hashes=["sha256:" + "b" * 64, "sha256:" + "c" * 64],
    )
    reversed_inputs = pipeline_run_idempotency_key(
        **common, input_hashes=["sha256:" + "c" * 64, "sha256:" + "b" * 64],
    )
    assert first != reversed_inputs


def test_explicit_retry_creates_new_queued_attempt_with_same_immutable_inputs():
    from sngso_engine.operations import create_pipeline_retry_run
    from sngso_engine.orchestrator import PipelineStage, PipelineStageFailure, execute_pipeline_run

    context = {"batch": "retry-fixture"}
    with SQLiteRegistry() as registry:
        first = _idempotent_run_payload()
        first["status"] = "queued"
        first["input_hashes"] = [__import__("sngso_engine").sha256_fingerprint(context)]
        created = create_pipeline_run_idempotently(
            registry, first, run_kind="daily-edition",
            actor="test", reason="Create first attempt.",
        )
        failed = execute_pipeline_run(
            registry, created["record"]["id"], input_context=context,
            stages=[PipelineStage(
                "network-stage",
                lambda _: (_ for _ in ()).throw(
                    PipelineStageFailure("network_timeout", "Temporary network timeout.")
                ),
            )],
            actor="test-worker", reason="Execute first attempt.",
        )
        assert failed["status"] == "failed"

        retried = create_pipeline_retry_run(
            registry, created["record"]["id"], run_kind="daily-edition",
            previous_attempt=1, recovery_confirmed=True, permissions_rechecked=True, actor="operator", reason="Authorize one deliberate retry.",
        )
        duplicate = create_pipeline_retry_run(
            registry, created["record"]["id"], run_kind="daily-edition",
            previous_attempt=1, recovery_confirmed=True, permissions_rechecked=True, actor="operator", reason="Repeat retry request.",
        )
        assert retried["created"] is True
        assert duplicate["created"] is False
        assert retried["attempt"] == 2
        assert retried["record"]["status"] == "queued"
        assert retried["record"]["input_hashes"] == created["record"]["input_hashes"]
        assert retried["record"]["config_hash"] == created["record"]["config_hash"]
        assert retried["record"]["initiated_by"] == created["record"]["initiated_by"]
        assert retried["retry_decision"]["retryable"] is True


@pytest.mark.parametrize("failure_code", ["permission_revoked", "unknown_failure"])
def test_explicit_retry_refuses_permanent_and_unknown_failures(failure_code):
    from sngso_engine.operations import create_pipeline_retry_run
    from sngso_engine.orchestrator import PipelineStage, PipelineStageFailure, execute_pipeline_run
    from sngso_engine.canonical import sha256_fingerprint

    context = {"batch": "no-retry"}
    payload = _idempotent_run_payload()
    payload["status"] = "queued"
    payload["input_hashes"] = [sha256_fingerprint(context)]
    with SQLiteRegistry() as registry:
        created = create_pipeline_run_idempotently(
            registry, payload, run_kind="daily-edition",
            actor="test", reason="Create no-retry fixture.",
        )
        execute_pipeline_run(
            registry, created["record"]["id"], input_context=context,
            stages=[PipelineStage(
                "failure",
                lambda _: (_ for _ in ()).throw(
                    PipelineStageFailure(failure_code, "Safe test failure.")
                ),
            )],
            actor="test-worker", reason="Fail no-retry fixture.",
        )
        with pytest.raises(OperationalReportError, match="not eligible for retry"):
            create_pipeline_retry_run(
                registry, created["record"]["id"], run_kind="daily-edition",
                previous_attempt=1, recovery_confirmed=True, permissions_rechecked=True, actor="operator", reason="Attempt forbidden retry.",
            )
        assert len(registry.list_pipeline_runs(limit=10)) == 1


def test_explicit_retry_requires_exact_previous_attempt_identity():
    from sngso_engine.operations import create_pipeline_retry_run
    from sngso_engine.orchestrator import PipelineStage, PipelineStageFailure, execute_pipeline_run
    from sngso_engine.canonical import sha256_fingerprint

    context = {"batch": "wrong-attempt"}
    payload = _idempotent_run_payload()
    payload["status"] = "queued"
    payload["input_hashes"] = [sha256_fingerprint(context)]
    with SQLiteRegistry() as registry:
        created = create_pipeline_run_idempotently(
            registry, payload, run_kind="daily-edition",
            actor="test", reason="Create attempt identity fixture.",
        )
        execute_pipeline_run(
            registry, created["record"]["id"], input_context=context,
            stages=[PipelineStage(
                "failure",
                lambda _: (_ for _ in ()).throw(
                    PipelineStageFailure("network_timeout", "Temporary failure.")
                ),
            )],
            actor="test-worker", reason="Fail attempt identity fixture.",
        )
        with pytest.raises(OperationalReportError, match="attempt number"):
            create_pipeline_retry_run(
                registry, created["record"]["id"], run_kind="daily-edition",
                previous_attempt=2, recovery_confirmed=True, permissions_rechecked=True, actor="operator", reason="Supply wrong attempt.",
            )


def test_explicit_retry_refuses_runs_that_are_not_failed():
    from sngso_engine.operations import create_pipeline_retry_run
    from sngso_engine.canonical import sha256_fingerprint

    context = {"batch": "still-running"}
    payload = _idempotent_run_payload()
    payload["input_hashes"] = [sha256_fingerprint(context)]
    with SQLiteRegistry() as registry:
        created = create_pipeline_run_idempotently(
            registry, payload, run_kind="daily-edition",
            actor="test", reason="Create active run.",
        )
        with pytest.raises(OperationalReportError, match="only a failed"):
            create_pipeline_retry_run(
                registry, created["record"]["id"], run_kind="daily-edition",
                previous_attempt=1, recovery_confirmed=True, permissions_rechecked=True, actor="operator", reason="Attempt active retry.",
            )


def test_idempotent_pipeline_creation_validates_actor_and_reason_even_on_reuse():
    with SQLiteRegistry() as registry:
        payload = _idempotent_run_payload()
        create_pipeline_run_idempotently(
            registry, payload, run_kind="daily-edition",
            actor="operator", reason="Create initial run.",
        )
        with pytest.raises(OperationalReportError, match="actor and reason"):
            create_pipeline_run_idempotently(
                registry, payload, run_kind="daily-edition",
                actor="", reason="Duplicate request.",
            )
        with pytest.raises(OperationalReportError, match="actor and reason"):
            create_pipeline_run_idempotently(
                registry, payload, run_kind="daily-edition",
                actor="operator", reason=" ",
            )


def test_explicit_retry_requires_recovery_and_permission_attestations():
    from sngso_engine.operations import create_pipeline_retry_run
    from sngso_engine.orchestrator import PipelineStage, PipelineStageFailure, execute_pipeline_run
    from sngso_engine.canonical import sha256_fingerprint

    context = {"batch": "attestation-required"}
    payload = _idempotent_run_payload()
    payload["status"] = "queued"
    payload["input_hashes"] = [sha256_fingerprint(context)]
    with SQLiteRegistry() as registry:
        created = create_pipeline_run_idempotently(
            registry, payload, run_kind="daily-edition",
            actor="test", reason="Create attestation fixture.",
        )
        execute_pipeline_run(
            registry, created["record"]["id"], input_context=context,
            stages=[PipelineStage(
                "failure",
                lambda _: (_ for _ in ()).throw(
                    PipelineStageFailure("network_timeout", "Temporary failure.")
                ),
            )],
            actor="test-worker", reason="Fail attestation fixture.",
        )
        with pytest.raises(OperationalReportError, match="attestations"):
            create_pipeline_retry_run(
                registry, created["record"]["id"], run_kind="daily-edition",
                previous_attempt=1, actor="operator", reason="Omit safety attestations.",
            )


def test_explicit_retry_refuses_an_exhausted_attempt_limit():
    from sngso_engine.operations import create_pipeline_retry_run
    from sngso_engine.orchestrator import PipelineStage, PipelineStageFailure, execute_pipeline_run
    from sngso_engine.canonical import sha256_fingerprint

    context = {"batch": "attempt-limit"}
    payload = _idempotent_run_payload()
    payload["status"] = "queued"
    payload["input_hashes"] = [sha256_fingerprint(context)]
    with SQLiteRegistry() as registry:
        created = create_pipeline_run_idempotently(
            registry, payload, run_kind="daily-edition",
            actor="test", reason="Create final allowed attempt.", attempt=3,
        )
        execute_pipeline_run(
            registry, created["record"]["id"], input_context=context,
            stages=[PipelineStage(
                "failure",
                lambda _: (_ for _ in ()).throw(
                    PipelineStageFailure("network_timeout", "Temporary failure.")
                ),
            )],
            actor="test-worker", reason="Fail final allowed attempt.",
        )
        with pytest.raises(OperationalReportError, match="attempt_limit_reached"):
            create_pipeline_retry_run(
                registry, created["record"]["id"], run_kind="daily-edition",
                previous_attempt=3, max_attempts=3,
                recovery_confirmed=True, permissions_rechecked=True,
                actor="operator", reason="Attempt beyond retry limit.",
            )
        assert len(registry.list_pipeline_runs(limit=10)) == 1

