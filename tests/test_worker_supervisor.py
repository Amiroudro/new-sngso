from __future__ import annotations

import json
import os
import sys

import pytest

from sngso_engine.worker_supervisor import (
    WorkerEntrypoint,
    WorkerSupervisor,
    WorkerSupervisorError,
)


def _request(payload=None, request_id='test-1'):
    return {'schema_version': '1', 'request_id': request_id, 'payload': payload or {}}


def _supervisor(code: str, **kwargs):
    return WorkerSupervisor(
        [WorkerEntrypoint("test_worker", (sys.executable, "-c", code))],
        **kwargs,
    )


def test_registered_child_receives_request_on_stdin_and_returns_validated_json():
    code = (
        "import json,sys; request=json.load(sys.stdin); "
        "print(json.dumps({'schema_version':'1','result':{'request_id':request['request_id']}}))"
    )
    result = _supervisor(code).run("test_worker", _request(request_id='req-1'))
    assert result.status == "succeeded"
    assert result.result == {"request_id": "req-1"}
    assert result.exit_code == 0
    assert result.termination == "none"


def test_unknown_entrypoint_and_oversized_request_fail_before_launch():
    supervisor = _supervisor("raise SystemExit(99)", max_request_bytes=32)
    with pytest.raises(WorkerSupervisorError, match="not registered"):
        supervisor.run("unregistered", {})
    with pytest.raises(ValueError, match="byte limit"):
        supervisor.run("test_worker", _request({'value': 'x' * 100}))


def test_child_timeout_uses_process_group_termination_escalation():
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)"
    result = _supervisor(
        code, timeout_seconds=0.15, termination_grace_seconds=0.1,
    ).run("test_worker", _request())
    assert result.status == "timeout"
    assert result.termination == "sigkill"
    assert result.exit_code is not None
    assert result.duration_seconds < 3


def test_nonzero_child_exit_never_returns_a_success_result():
    code = "print('not a result'); raise SystemExit(7)"
    result = _supervisor(code).run("test_worker", _request())
    assert result.status == "failed"
    assert result.exit_code == 7
    assert result.result is None


def test_malformed_or_unexpected_result_envelope_fails_closed():
    code = "import json; print(json.dumps({'schema_version':'1','result':{},'extra':'no'}))"
    result = _supervisor(code).run("test_worker", _request())
    assert result.status == "protocol_error"
    assert result.result is None


def test_stdout_is_drained_but_capture_is_bounded_and_marked_truncated():
    code = (
        "import sys; sys.stdout.write('x' * 200000); sys.stdout.flush()"
    )
    result = _supervisor(code, max_stdout_bytes=1024).run(
        "test_worker", _request(),
    )
    assert result.status == "protocol_error"
    assert result.stdout_truncated is True
    assert result.result is None


def test_stderr_capture_is_bounded_without_blocking_child():
    code = (
        "import sys,json; sys.stderr.write('e' * 200000); sys.stderr.flush(); "
        "print(json.dumps({'schema_version':'1','result':{'ok':True}}))"
    )
    result = _supervisor(code, max_stderr_bytes=512).run(
        "test_worker", _request(),
    )
    assert result.status == "succeeded"
    assert result.stderr_truncated is True
    assert result.result == {"ok": True}


def test_request_is_not_interpolated_into_command_arguments():
    marker = "UNTRUSTED-ARGUMENT-MARKER"
    code = (
        "import json,sys; request=json.load(sys.stdin); "
        "print(json.dumps({'schema_version':'1','result':{'value':request['payload']['value']}}))"
    )
    result = _supervisor(code).run(
        "test_worker", _request({'value': marker}),
    )
    assert result.status == "succeeded"
    assert result.result == {"value": marker}


def test_environment_is_explicit_and_does_not_inherit_parent_environment():
    code = (
        "import json,os; print(json.dumps({'schema_version':'1','result':"
        "{'allowed':os.getenv('SNGSO_WORKER_TEST'),'ambient':os.getenv('HOME')}}))"
    )
    supervisor = WorkerSupervisor([
        WorkerEntrypoint(
            "test_worker", (sys.executable, "-c", code),
            environment={"SNGSO_WORKER_TEST": "allowed"},
        )
    ])
    result = supervisor.run("test_worker", _request())
    assert result.status == "succeeded"
    assert result.result == {"allowed": "allowed", "ambient": None}


def test_duplicate_entrypoints_are_rejected():
    entry = WorkerEntrypoint("same", (sys.executable, "-c", "pass"))
    with pytest.raises(ValueError, match="unique"):
        WorkerSupervisor([entry, entry])


def test_request_schema_rejects_missing_or_unexpected_envelope_fields():
    supervisor = _supervisor("raise SystemExit(99)")
    with pytest.raises(ValueError, match="registered schema"):
        supervisor.run("test_worker", {"schema_version": "1", "payload": {}})
    with pytest.raises(ValueError, match="registered schema"):
        supervisor.run("test_worker", {**_request(), "token": "must-not-pass"})


def test_registered_result_schema_is_enforced():
    code = (
        "import json; print(json.dumps({'schema_version':'1','result':{'unexpected':'x'}}))"
    )
    entry = WorkerEntrypoint(
        "test_worker", (sys.executable, "-c", code),
        result_schema={
            "type": "object",
            "required": ["count"],
            "additionalProperties": False,
            "properties": {"count": {"type": "integer", "minimum": 0}},
        },
    )
    result = WorkerSupervisor([entry]).run("test_worker", _request())
    assert result.status == "protocol_error"
    assert result.result is None


def test_child_that_leaves_descendant_holding_pipes_is_failed_and_group_cleaned():
    code = (
        "import subprocess,sys,json; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
        "print(json.dumps({'schema_version':'1','result':{'ok':True}}))"
    )
    result = _supervisor(code, timeout_seconds=2).run("test_worker", _request())
    assert result.status == "failed"
    assert result.termination == "pipe_cleanup_unconfirmed"
    assert result.duration_seconds < 4


def test_entrypoint_requires_absolute_executable_path():
    with pytest.raises(ValueError, match="absolute path"):
        WorkerEntrypoint("relative", ("python", "-c", "pass"))


def _lease_fixture(registry):
    from datetime import datetime, timezone
    from sngso_engine import WorkerLeaseStore, sha256_fingerprint

    run_id = "pipeline-run:supervised-worker"
    context = {"test": "supervised-worker"}
    registry.add_pipeline_run({
        "schema_version": "1.0.0", "id": run_id, "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "supervisor-test/1.0",
        "config_hash": "sha256:" + "b" * 64,
        "input_hashes": [sha256_fingerprint(context)],
        "initiated_by": "test-scheduler",
    }, actor="test_scheduler", reason="create_supervised_worker_test")
    store = WorkerLeaseStore(registry)
    lease = store.acquire(
        run_id, "worker_supervised", actor="scheduler",
        reason="supervised_worker_claim", ttl_seconds=10,
    )
    return store, lease


def test_supervisor_hands_lease_only_through_private_fd_and_heartbeats():
    import json
    import time
    from sngso_engine import SQLiteRegistry, WorkerLeaseSession

    code = (
        "import json,os,time; "
        "data=json.loads(os.read(int(os.environ['SNGSO_WORKER_CONTROL_FD']),8192)); "
        "time.sleep(0.18); "
        "print(json.dumps({'schema_version':'1','result':"
        "{'worker':data['worker_id'],'generation':data['fencing_token']}}))"
    )
    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        try:
            result = _supervisor(code).run(
                "test_worker", _request(), lease_session=session,
            )
            assert result.status == "succeeded"
            assert result.result == {"worker": "worker_supervised", "generation": 1}
            assert lease.token not in json.dumps(result.result)
            assert session.lease.revision >= 2
        finally:
            session.close()


def test_supervisor_propagates_durable_cancellation_and_requires_child_ack():
    import threading
    import time
    from sngso_engine import SQLiteRegistry, WorkerLeaseSession, WorkerLeaseStore

    code = """import json, os, select, time
control = json.loads(os.read(int(os.environ['SNGSO_WORKER_CONTROL_FD']), 8192))
cancel = int(os.environ['SNGSO_WORKER_CANCEL_FD'])
deadline = time.monotonic() + 3
acknowledged = False
while time.monotonic() < deadline:
    readable, _, _ = select.select([cancel], [], [], 0.02)
    if readable and os.read(cancel, 1) == bytes([1]):
        acknowledged = True
        break
print(json.dumps({'schema_version': '1', 'result': {
    'cancellation_acknowledged': acknowledged,
    'generation': control['fencing_token'],
}}))
"""
    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        def request_cancel():
            time.sleep(0.15)
            store.request_cancellation(
                lease.pipeline_run_id, actor="operator",
                reason_code="test_cancel", permission_check=lambda: True,
            )
        thread = threading.Thread(target=request_cancel)
        thread.start()
        try:
            result = _supervisor(code, timeout_seconds=2).run(
                "test_worker", _request(), lease_session=session,
            )
            thread.join(timeout=2)
            assert result.status == "cancelled"
            assert result.result == {"generation": 1}
            assert store.history(lease.pipeline_run_id)[-1]["status"] == "active"
        finally:
            session.close(release=False)


def test_supervisor_does_not_treat_missing_child_ack_as_successful_cancellation():
    import threading
    import time
    from sngso_engine import SQLiteRegistry, WorkerLeaseSession

    code = "import time; time.sleep(5)"
    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        def request_cancel():
            time.sleep(0.12)
            store.request_cancellation(
                lease.pipeline_run_id, actor="operator",
                reason_code="test_cancel", permission_check=lambda: True,
            )
        thread = threading.Thread(target=request_cancel)
        thread.start()
        try:
            result = _supervisor(
                code, timeout_seconds=2, termination_grace_seconds=0.1,
            ).run("test_worker", _request(), lease_session=session)
            thread.join(timeout=2)
            assert result.status == "cancelled_unacknowledged"
            assert result.result is None
            assert store.history(lease.pipeline_run_id)[-1]["status"] == "active"
        finally:
            session.close(release=False)


def test_supervisor_terminates_child_when_lease_heartbeat_is_lost():
    import threading
    import time
    from sngso_engine import SQLiteRegistry, WorkerLeaseSession

    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        def revoke_lease():
            time.sleep(0.15)
            store.release(lease, actor=lease.worker_id, reason="test_revoke")
        thread = threading.Thread(target=revoke_lease)
        thread.start()
        try:
            result = _supervisor(
                "import time; time.sleep(5)", timeout_seconds=2,
            ).run("test_worker", _request(), lease_session=session)
            thread.join(timeout=2)
            assert result.status == "lease_lost"
            assert result.result is None
        finally:
            session.close(release=False)


def test_entrypoint_cannot_override_reserved_control_descriptor_environment():
    code = "raise SystemExit(0)"
    with pytest.raises(ValueError, match="reserved worker control"):
        WorkerEntrypoint(
            "test_worker", (sys.executable, "-c", code),
            environment={"SNGSO_WORKER_CONTROL_FD": "999"},
        )


def test_supervised_worker_handler_commits_only_validated_child_output_to_checkpoint():
    import json
    from sngso_engine import (
        SQLiteRegistry, StageCheckpointStore, WorkerLeaseSession,
        execute_checkpointed_stage, supervised_checkpoint_handler,
    )

    code = (
        "import json,sys; request=json.load(sys.stdin); "
        "print(json.dumps({'schema_version':'1','result':"
        "{'processed':3,'key':request['payload']['idempotency_key']}}))"
    )
    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        try:
            handler = supervised_checkpoint_handler(
                _supervisor(code), "test_worker",
                lambda context, key: _request({"idempotency_key": key}),
                session,
            )
            result = execute_checkpointed_stage(
                registry, lease.pipeline_run_id, 0, "normalize",
                {"input": {"safe": True}}, handler,
                actor=lease.worker_id, reason="supervised_stage_success",
                permission_check=lambda: True, replay_safe=False,
                lease_guard=session.assert_healthy,
            )
            checkpoint = StageCheckpointStore(registry).get(lease.pipeline_run_id, 0)
            assert result["status"] == "succeeded"
            assert result["output"] == {
                "processed": 3, "key": result["idempotency_key"],
            }
            assert checkpoint.status == "succeeded"
            assert checkpoint.output_fingerprint == result["output_fingerprint"]
        finally:
            session.close()


def test_supervised_worker_timeout_leaves_side_effecting_checkpoint_indeterminate():
    import pytest
    from sngso_engine import (
        SQLiteRegistry, StageCheckpointError, StageCheckpointStore,
        WorkerLeaseSession, WorkerProcessExecutionError,
        execute_checkpointed_stage, supervised_checkpoint_handler,
    )

    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        try:
            handler = supervised_checkpoint_handler(
                _supervisor("import time; time.sleep(5)", timeout_seconds=0.1,
                            termination_grace_seconds=0.05),
                "test_worker", lambda context, key: _request(), session,
            )
            with pytest.raises(StageCheckpointError) as captured:
                execute_checkpointed_stage(
                    registry, lease.pipeline_run_id, 0, "remote_write",
                    {"input": {"safe": True}}, handler,
                    actor=lease.worker_id, reason="supervised_stage_timeout",
                    permission_check=lambda: True, replay_safe=False,
                    provider_namespace="provider_x",
                    lease_guard=session.assert_healthy,
                )
            checkpoint = StageCheckpointStore(registry).get(lease.pipeline_run_id, 0)
            assert checkpoint.status == "indeterminate"
            assert isinstance(captured.value.__cause__, WorkerProcessExecutionError)
            assert captured.value.__cause__.process_result.status == "timeout"
        finally:
            session.close(release=False)


def test_lease_lost_child_cannot_mark_its_checkpoint_failed_or_indeterminate():
    import threading
    import time
    import pytest
    from sngso_engine import (
        LeaseLostError, SQLiteRegistry, StageCheckpointStore,
        WorkerLeaseSession, execute_checkpointed_stage,
        supervised_checkpoint_handler,
    )

    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        def revoke_lease():
            time.sleep(0.15)
            store.release(lease, actor=lease.worker_id, reason="test_revoke")
        thread = threading.Thread(target=revoke_lease)
        thread.start()
        try:
            handler = supervised_checkpoint_handler(
                _supervisor("import time; time.sleep(5)", timeout_seconds=2),
                "test_worker", lambda context, key: _request(), session,
            )
            with pytest.raises(LeaseLostError):
                execute_checkpointed_stage(
                    registry, lease.pipeline_run_id, 0, "remote_write",
                    {"input": {"safe": True}}, handler,
                    actor=lease.worker_id, reason="lease_lost_supervised_stage",
                    permission_check=lambda: True, replay_safe=False,
                    provider_namespace="provider_x",
                    lease_guard=session.assert_healthy,
                )
            thread.join(timeout=2)
            checkpoint = StageCheckpointStore(registry).get(lease.pipeline_run_id, 0)
            assert checkpoint.status == "executing"
        finally:
            session.close(release=False)


def test_supervisor_kills_child_when_point_of_use_lease_check_raises():
    import pytest
    from sngso_engine import LeaseLostError, SQLiteRegistry, WorkerLeaseSession

    with SQLiteRegistry() as registry:
        store, lease = _lease_fixture(registry)
        session = WorkerLeaseSession(
            store, lease, heartbeat_interval_seconds=0.05, ttl_seconds=10,
        ).start()
        session.check_cancellation = lambda: (_ for _ in ()).throw(
            LeaseLostError("simulated lease loss at cancellation poll")
        )
        try:
            result = _supervisor(
                "import time; time.sleep(5)", timeout_seconds=2,
            ).run("test_worker", _request(), lease_session=session)
            assert result.status == "lease_lost"
            assert result.result is None
            assert result.termination in {"sigterm", "sigkill"}
        finally:
            session.close(release=False)
