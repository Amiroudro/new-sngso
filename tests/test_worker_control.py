from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from sngso_engine.worker_control import (
    WorkerControlError,
    create_worker_control_channel,
    read_worker_control,
    worker_cancellation_requested,
)


class _Lease:
    pipeline_run_id = "run-123"
    worker_id = "worker-a"
    fencing_token = 7
    token = "sensitive-bearer-token-" + ("x" * 40)


def test_private_pipe_handoff_contains_lease_and_fencing_metadata():
    channel = create_worker_control_channel(_Lease())
    try:
        control = read_worker_control(channel.control_read_fd)
        assert control.pipeline_run_id == "run-123"
        assert control.worker_id == "worker-a"
        assert control.fencing_token == 7
        assert control.lease_token == _Lease.token
    finally:
        channel.close()


def test_cancellation_pipe_is_cooperative_and_one_byte():
    channel = create_worker_control_channel(_Lease())
    try:
        assert worker_cancellation_requested(channel.cancellation_read_fd) is False
        channel.request_cancellation()
        assert worker_cancellation_requested(channel.cancellation_read_fd) is True
        assert worker_cancellation_requested(channel.cancellation_read_fd) is False
    finally:
        channel.close()


def test_child_receives_secret_only_through_inherited_fd_not_argv_or_environment():
    channel = create_worker_control_channel(_Lease())
    control_fd, cancel_fd = channel.child_fds
    code = (
        "import json,os; "
        "data=json.loads(os.read(int(os.environ['CONTROL_FD']),8192)); "
        "print(json.dumps({'worker':data['worker_id'],'generation':data['fencing_token']}))"
    )
    args = [sys.executable, "-c", code, str(control_fd), str(cancel_fd)]
    env = {"CONTROL_FD": str(control_fd), "CANCEL_FD": str(cancel_fd)}
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, close_fds=True, pass_fds=(control_fd, cancel_fd),
        )
        channel.close_child_handles()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr.decode()
        assert json.loads(stdout) == {"worker": "worker-a", "generation": 7}
        assert _Lease.token.encode() not in stdout
        assert _Lease.token not in args
        assert all(_Lease.token not in value for value in env.values())
    finally:
        channel.close()


def test_channel_close_is_idempotent_and_descriptors_are_not_reusable():
    channel = create_worker_control_channel(_Lease())
    descriptors = channel.child_fds
    channel.close()
    channel.close()
    with pytest.raises(WorkerControlError, match="closed"):
        _ = channel.child_fds
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_invalid_lease_metadata_is_rejected_before_pipe_creation():
    class Invalid:
        pipeline_run_id = "../unsafe"
        worker_id = "worker"
        fencing_token = 1
        token = "x" * 40

    with pytest.raises(ValueError, match="invalid"):
        create_worker_control_channel(Invalid())


def test_cancellation_timeout_is_bounded_and_validated():
    channel = create_worker_control_channel(_Lease())
    try:
        assert worker_cancellation_requested(channel.cancellation_read_fd, timeout_seconds=0.01) is False
        with pytest.raises(ValueError):
            worker_cancellation_requested(channel.cancellation_read_fd, timeout_seconds=-1)
    finally:
        channel.close()
