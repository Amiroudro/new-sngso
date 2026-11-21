from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sngso_engine.operations import OperationalReportError, build_operational_health_report
from sngso_engine.registry import SQLiteRegistry


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
