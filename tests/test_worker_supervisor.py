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
    result = _supervisor(code).run("test_worker", {"schema_version": "1", "request_id": "req-1"})
    assert result.status == "succeeded"
    assert result.result == {"request_id": "req-1"}
    assert result.exit_code == 0
    assert result.termination == "none"


def test_unknown_entrypoint_and_oversized_request_fail_before_launch():
    supervisor = _supervisor("raise SystemExit(99)", max_request_bytes=32)
    with pytest.raises(WorkerSupervisorError, match="not registered"):
        supervisor.run("unregistered", {})
    with pytest.raises(ValueError, match="byte limit"):
        supervisor.run("test_worker", {"payload": "x" * 100})


def test_child_timeout_uses_process_group_termination_escalation():
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)"
    result = _supervisor(
        code, timeout_seconds=0.15, termination_grace_seconds=0.1,
    ).run("test_worker", {"schema_version": "1"})
    assert result.status == "timeout"
    assert result.termination == "sigkill"
    assert result.exit_code is not None
    assert result.duration_seconds < 3


def test_nonzero_child_exit_never_returns_a_success_result():
    code = "print('not a result'); raise SystemExit(7)"
    result = _supervisor(code).run("test_worker", {"schema_version": "1"})
    assert result.status == "failed"
    assert result.exit_code == 7
    assert result.result is None


def test_malformed_or_unexpected_result_envelope_fails_closed():
    code = "print(json.dumps({'schema_version':'1','result':{},'extra':'no'}))"
    result = _supervisor(code).run("test_worker", {"schema_version": "1"})
    assert result.status == "protocol_error"
    assert result.result is None


def test_stdout_is_drained_but_capture_is_bounded_and_marked_truncated():
    code = (
        "import sys; sys.stdout.write('x' * 200000); sys.stdout.flush()"
    )
    result = _supervisor(code, max_stdout_bytes=1024).run(
        "test_worker", {"schema_version": "1"},
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
        "test_worker", {"schema_version": "1"},
    )
    assert result.status == "succeeded"
    assert result.stderr_truncated is True
    assert result.result == {"ok": True}


def test_request_is_not_interpolated_into_command_arguments():
    marker = "UNTRUSTED-ARGUMENT-MARKER"
    code = (
        "import json,sys; request=json.load(sys.stdin); "
        "print(json.dumps({'schema_version':'1','result':{'value':request['value']}}))"
    )
    result = _supervisor(code).run(
        "test_worker", {"schema_version": "1", "value": marker},
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
    result = supervisor.run("test_worker", {"schema_version": "1"})
    assert result.status == "succeeded"
    assert result.result == {"allowed": "allowed", "ambient": None}


def test_duplicate_entrypoints_are_rejected():
    entry = WorkerEntrypoint("same", (sys.executable, "-c", "pass"))
    with pytest.raises(ValueError, match="unique"):
        WorkerSupervisor([entry, entry])
