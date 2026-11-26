from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sngso_engine.worker_observability import WorkerTelemetry, WorkerTelemetryError


def result(**changes):
    values = {
        "entrypoint": "collect",
        "status": "succeeded",
        "termination": "none",
        "duration_seconds": 0.25,
        "exit_code": 0,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_worker_telemetry_records_bounded_aggregate_and_recent_outcomes():
    telemetry = WorkerTelemetry(("collect", "normalize"), max_events=2)
    telemetry.record_result(result())
    telemetry.record_result(result(status="timeout", termination="sigterm", exit_code=-15,
                                   duration_seconds=2.0))
    telemetry.record_result(result(entrypoint="normalize", duration_seconds=700.0))
    snapshot = telemetry.snapshot()
    assert snapshot["counts_by_entrypoint"]["collect"] == {
        "succeeded": 1, "timeout": 1,
    }
    assert snapshot["latency"]["collect"]["count"] == 2
    assert snapshot["latency"]["collect"]["buckets_le_seconds"]["0.5"] == 1
    assert snapshot["latency"]["collect"]["buckets_le_seconds"]["5.0"] == 1
    assert snapshot["latency"]["normalize"]["buckets_gt_last"] == 1
    assert len(snapshot["recent_outcomes"]) == 2
    assert snapshot["production_backend_configured"] is False


def test_worker_telemetry_rejects_unregistered_and_unknown_labels():
    telemetry = WorkerTelemetry(("collect",))
    with pytest.raises(WorkerTelemetryError):
        telemetry.record_result(result(entrypoint="attacker"))
    with pytest.raises(WorkerTelemetryError):
        telemetry.record_result(result(status="arbitrary-label"))
    with pytest.raises(WorkerTelemetryError):
        telemetry.record_result(result(termination="secret-value"))


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), True])
def test_worker_telemetry_rejects_invalid_duration(duration):
    telemetry = WorkerTelemetry(("collect",))
    with pytest.raises(WorkerTelemetryError):
        telemetry.record_result(result(duration_seconds=duration))


def test_worker_telemetry_validates_configuration():
    with pytest.raises(WorkerTelemetryError):
        WorkerTelemetry(())
    with pytest.raises(WorkerTelemetryError):
        WorkerTelemetry(("collect", "collect"))
    with pytest.raises(WorkerTelemetryError):
        WorkerTelemetry(("Collect",))
    with pytest.raises(WorkerTelemetryError):
        WorkerTelemetry(("collect",), max_events=10_001)
    with pytest.raises(WorkerTelemetryError):
        WorkerTelemetry(("collect",), latency_buckets=(1.0, 0.5))


def test_worker_telemetry_concurrent_updates_are_not_lost():
    telemetry = WorkerTelemetry(("collect",), max_events=32)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: telemetry.record_result(result()), range(500)))
    snapshot = telemetry.snapshot()
    assert snapshot["counts_by_entrypoint"]["collect"]["succeeded"] == 500
    assert snapshot["latency"]["collect"]["count"] == 500
    assert len(snapshot["recent_outcomes"]) == 32


def test_worker_telemetry_does_not_retain_request_or_identity_fields():
    telemetry = WorkerTelemetry(("collect",))
    telemetry.record_result(result(request={"password": "never-store"},
                                   worker_id="private-worker", run_id="private-run"))
    snapshot = telemetry.snapshot()
    rendered = repr(snapshot)
    assert "password" not in rendered
    assert "private-worker" not in rendered
    assert "private-run" not in rendered
