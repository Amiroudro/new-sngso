"""Protected POSIX file-descriptor handoff for supervised worker lease metadata.

The parent sends the bearer token only through a private inherited pipe. Descriptor
numbers may be placed in a child's explicitly constructed environment; credentials
must never be copied into argv, environment values, stdin, or diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import select
from typing import Any


_MAX_CONTROL_BYTES = 8192


class WorkerControlError(RuntimeError):
    """Invalid or unavailable protected worker-control channel."""


@dataclass(frozen=True)
class WorkerControl:
    pipeline_run_id: str
    worker_id: str
    fencing_token: int
    lease_token: str


@dataclass
class WorkerControlChannel:
    """Parent-side write handles and child-side inherited read descriptors."""

    control_read_fd: int
    control_write_fd: int
    cancellation_read_fd: int
    cancellation_write_fd: int
    _closed: bool = False

    @property
    def child_fds(self) -> tuple[int, int]:
        if self._closed:
            raise WorkerControlError("worker control channel is closed")
        return self.control_read_fd, self.cancellation_read_fd

    def close_child_handles(self) -> None:
        """Call in the parent immediately after Popen has inherited pass_fds."""
        for name in ("control_read_fd", "cancellation_read_fd"):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, name, -1)

    def request_cancellation(self) -> None:
        if self._closed or self.cancellation_write_fd < 0:
            raise WorkerControlError("cancellation channel is closed")
        try:
            os.write(self.cancellation_write_fd, b"\\x01")
        except BlockingIOError as exc:
            raise WorkerControlError("cancellation channel is not writable") from exc
        except OSError as exc:
            raise WorkerControlError("worker cancellation channel is unavailable") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for name in (
            "control_read_fd", "control_write_fd",
            "cancellation_read_fd", "cancellation_write_fd",
        ):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                setattr(self, name, -1)

    def __enter__(self) -> "WorkerControlChannel":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def create_worker_control_channel(lease: Any) -> WorkerControlChannel:
    """Create pipes and write a bounded, versioned lease envelope to the private pipe."""
    required = ("pipeline_run_id", "worker_id", "fencing_token", "token")
    if any(not hasattr(lease, field) for field in required):
        raise TypeError("lease must expose run, worker, fencing generation, and token fields")
    run_id, worker_id, token = lease.pipeline_run_id, lease.worker_id, lease.token
    generation = lease.fencing_token
    if (not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", run_id)
            or not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", worker_id)
            or not isinstance(token, str) or not 32 <= len(token) <= 256
            or isinstance(generation, bool) or not isinstance(generation, int) or generation < 1):
        raise ValueError("lease control metadata is invalid")
    payload = json.dumps(
        {"schema_version": "1", "pipeline_run_id": run_id, "worker_id": worker_id,
         "fencing_token": generation, "lease_token": token},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if len(payload) > _MAX_CONTROL_BYTES:
        raise ValueError("lease control envelope exceeds its byte limit")
    control_read, control_write = os.pipe()
    cancel_read, cancel_write = os.pipe()
    try:
        os.write(control_write, payload)
        os.close(control_write)
        control_write = -1
        os.set_blocking(cancel_write, False)
        return WorkerControlChannel(
            control_read, control_write, cancel_read, cancel_write,
        )
    except Exception:
        for descriptor in (control_read, control_write, cancel_read, cancel_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        raise


def read_worker_control(control_fd: int) -> WorkerControl:
    """Child-side one-shot reader. The parent closes its writer to delimit the frame."""
    chunks = bytearray()
    while True:
        chunk = os.read(control_fd, min(2048, _MAX_CONTROL_BYTES + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > _MAX_CONTROL_BYTES:
            raise WorkerControlError("worker control envelope exceeds its byte limit")
    try:
        value = json.loads(bytes(chunks).decode("utf-8"))
        if (not isinstance(value, dict)
                or set(value) != {"schema_version", "pipeline_run_id", "worker_id",
                                  "fencing_token", "lease_token"}
                or value["schema_version"] != "1"
                or not isinstance(value["pipeline_run_id"], str)
                or not isinstance(value["worker_id"], str)
                or isinstance(value["fencing_token"], bool)
                or not isinstance(value["fencing_token"], int)
                or not isinstance(value["lease_token"], str)):
            raise ValueError
        return WorkerControl(
            value["pipeline_run_id"], value["worker_id"],
            value["fencing_token"], value["lease_token"],
        )
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise WorkerControlError("worker control envelope is malformed") from exc


def worker_cancellation_requested(cancel_fd: int, *, timeout_seconds: float = 0.0) -> bool:
    """Return true when the parent has sent the cooperative cancellation byte."""
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds < 0 or timeout_seconds > 3600):
        raise ValueError("timeout_seconds must be between zero and 3600")
    readable, _, _ = select.select([cancel_fd], [], [], timeout_seconds)
    if not readable:
        return False
    try:
        value = os.read(cancel_fd, 1)
    except OSError as exc:
        raise WorkerControlError("worker cancellation channel is unavailable") from exc
    return value == b"\\x01"
