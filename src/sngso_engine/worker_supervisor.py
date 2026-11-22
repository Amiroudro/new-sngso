"""Bounded subprocess supervision for statically registered worker entrypoints.

This is a process-boundary primitive, not a distributed scheduler. It accepts
only preconfigured argv vectors, sends bounded JSON requests over stdin, drains
bounded diagnostic output, and never invokes a shell.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import shutil
import signal
import sys
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from .worker_control import create_worker_control_channel
from .worker_leases import LeaseLostError, WorkerLeaseSession


class WorkerSupervisorError(RuntimeError):
    """Base error for invalid supervisor configuration or requests."""


@dataclass(frozen=True)
class WorkerResourceLimits:
    """Kernel-enforced Linux limits applied before the worker executable starts.

    Values are hard and soft limits set together by util-linux prlimit(1).
    They are deliberately opt-in; this class never silently falls back to an
    unrestricted launch when the platform helper is unavailable.
    """

    cpu_seconds: int = 60
    address_space_bytes: int = 1_073_741_824
    open_files: int = 128

    def __post_init__(self) -> None:
        if (isinstance(self.cpu_seconds, bool) or not isinstance(self.cpu_seconds, int)
                or not 1 <= self.cpu_seconds <= 86_400):
            raise ValueError("cpu_seconds must be an integer from 1 to 86400")
        if (isinstance(self.address_space_bytes, bool)
                or not isinstance(self.address_space_bytes, int)
                or not 16_777_216 <= self.address_space_bytes <= 17_179_869_184):
            raise ValueError("address_space_bytes must be from 16 MiB to 16 GiB")
        if (isinstance(self.open_files, bool) or not isinstance(self.open_files, int)
                or not 16 <= self.open_files <= 4096):
            raise ValueError("open_files must be an integer from 16 to 4096")


@dataclass(frozen=True)
class WorkerEntrypoint:
    """A named, preconfigured executable command. Never derive argv from requests."""

    name: str
    argv: tuple[str, ...]
    environment: Mapping[str, str] | None = None
    request_schema: Mapping[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "required": ["schema_version", "request_id", "payload"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": "1"},
            "request_id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,100}$"},
            "payload": {"type": "object"},
        },
    })
    result_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    resource_limits: WorkerResourceLimits | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", self.name):
            raise ValueError("entrypoint name must be a bounded lowercase identifier")
        if not isinstance(self.argv, tuple) or not self.argv or len(self.argv) > 32:
            raise ValueError("entrypoint argv must be a non-empty tuple of at most 32 arguments")
        if not os.path.isabs(self.argv[0]):
            raise ValueError("entrypoint executable must be an absolute path")
        for item in self.argv:
            if not isinstance(item, str) or not item or "\x00" in item or len(item) > 4096:
                raise ValueError("entrypoint argv contains an invalid argument")
        if self.resource_limits is not None and not isinstance(self.resource_limits, WorkerResourceLimits):
            raise ValueError("resource_limits must be a WorkerResourceLimits instance")
        for schema in (self.request_schema, self.result_schema):
            if not isinstance(schema, Mapping):
                raise ValueError("worker request and result schemas must be JSON Schema objects")
            Draft202012Validator.check_schema(schema)
        if self.environment is not None:
            if not isinstance(self.environment, Mapping) or len(self.environment) > 30:
                raise ValueError("entrypoint environment must be a bounded mapping")
            if {"SNGSO_WORKER_CONTROL_FD", "SNGSO_WORKER_CANCEL_FD"}.intersection(self.environment):
                raise ValueError("entrypoint environment cannot override reserved worker control descriptors")
            for key, value in self.environment.items():
                if (not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key)
                        or not isinstance(value, str) or "\x00" in value or len(value) > 4096):
                    raise ValueError("entrypoint environment contains an invalid value")


@dataclass(frozen=True)
class WorkerProcessResult:
    entrypoint: str
    status: str
    exit_code: int | None
    result: Mapping[str, Any] | None
    duration_seconds: float
    stdout_truncated: bool
    stderr_truncated: bool
    termination: str


def _bounded_json(value: Mapping[str, Any], max_bytes: int) -> bytes:
    if not isinstance(value, Mapping):
        raise ValueError("worker request must be a JSON object")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("worker request is not valid bounded JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError("worker request exceeds the configured byte limit")
    return encoded


class _BoundedDrain(threading.Thread):
    """Continuously drain a pipe while retaining no more than limit bytes."""

    def __init__(self, stream: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.data = bytearray()
        self.truncated = False
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read(8192)
                if not chunk:
                    return
                remaining = self.limit - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        except Exception as exc:  # diagnostic only; never persist exception text
            self.error = exc


class _StdinWriter(threading.Thread):
    def __init__(self, stream: Any, payload: bytes) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.payload = payload
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.stream.write(self.payload)
            self.stream.flush()
        except (BrokenPipeError, OSError) as exc:
            self.error = exc
        finally:
            try:
                self.stream.close()
            except OSError:
                pass


class WorkerSignalShutdown:
    """Translate process signals into normal-thread bounded supervisor shutdown.

    Signal handlers only set an event. A dedicated control thread performs
    process-group termination and bounded waits outside signal context.
    Installation/restoration must occur on the main thread, as required by
    Python's signal module.
    """

    def __init__(
        self,
        supervisor: "WorkerSupervisor",
        *,
        signals: Sequence[int] = (signal.SIGTERM, signal.SIGINT),
        timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(supervisor, WorkerSupervisor):
            raise TypeError("supervisor must be a WorkerSupervisor")
        if (isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not 0.0 <= timeout_seconds <= 60.0):
            raise ValueError("signal shutdown timeout must be between zero and 60")
        normalized = tuple(signals)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("signals must be a non-empty unique sequence")
        for signum in normalized:
            if isinstance(signum, bool) or not isinstance(signum, int) or signum not in {
                signal.SIGTERM, signal.SIGINT,
            }:
                raise ValueError("only SIGTERM and SIGINT may trigger worker shutdown")
        self.supervisor = supervisor
        self.signals = normalized
        self.timeout_seconds = float(timeout_seconds)
        self._received = threading.Event()
        self._stop = threading.Event()
        self._completed = threading.Event()
        self._previous: dict[int, Any] = {}
        self._thread: threading.Thread | None = None
        self._received_signal: int | None = None
        self._shutdown_result: bool | None = None
        self._error: Exception | None = None

    @property
    def received_signal(self) -> int | None:
        return self._received_signal

    @property
    def shutdown_complete(self) -> bool:
        return self._completed.is_set()

    @property
    def shutdown_result(self) -> bool | None:
        return self._shutdown_result

    @property
    def shutdown_error(self) -> Exception | None:
        return self._error

    def _handle_signal(self, signum: int, frame: Any) -> None:
        # Deliberately do not wait, access the registry, or terminate children
        # in signal context. The normal control thread performs all cleanup.
        self._received_signal = signum
        self._received.set()

    def _control_loop(self) -> None:
        if self._received.wait():
            try:
                self._shutdown_result = self.supervisor.shutdown(
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception as exc:
                self._error = exc
            finally:
                self._completed.set()

    def start(self) -> "WorkerSignalShutdown":
        if threading.current_thread() is not threading.main_thread():
            raise WorkerSupervisorError("signal handlers must be installed from the main thread")
        if self._thread is not None:
            raise WorkerSupervisorError("signal shutdown adapter has already been started")
        try:
            for signum in self.signals:
                self._previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
        except Exception:
            for signum, previous in self._previous.items():
                signal.signal(signum, previous)
            self._previous.clear()
            raise
        self._thread = threading.Thread(
            target=self._control_loop, name="worker-signal-shutdown", daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise WorkerSupervisorError("signal handlers must be restored from the main thread")
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)
        self._previous.clear()
        if self._thread is not None:
            if not self._received.is_set():
                self._stop.set()
                # Wake a control thread that is waiting for a signal but whose
                # adapter is being closed without any shutdown request.
                self._received.set()
                self._shutdown_result = None
                self._completed.set()
            self._thread.join(timeout=self.timeout_seconds + 1.0)
            if self._thread.is_alive():
                raise WorkerSupervisorError("signal shutdown control thread did not stop within its bound")
            self._thread = None

    def __enter__(self) -> "WorkerSignalShutdown":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class WorkerSupervisor:
    """Supervise one registered child process with a hard wall-clock bound.

    POSIX process groups are required so descendants cannot outlive a timed-out
    worker while holding inherited stdout/stderr pipes. This first implementation
    fails closed on non-POSIX platforms instead of claiming equivalent semantics.
    """

    def __init__(
        self,
        entrypoints: Sequence[WorkerEntrypoint],
        *,
        timeout_seconds: float = 30.0,
        termination_grace_seconds: float = 2.0,
        max_request_bytes: int = 32_768,
        max_stdout_bytes: int = 65_536,
        max_stderr_bytes: int = 8_192,
    ) -> None:
        if os.name != "posix":
            raise WorkerSupervisorError("this supervisor implementation currently requires POSIX process groups")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.05 <= timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 0.05 and 3600")
        if isinstance(termination_grace_seconds, bool) or not isinstance(termination_grace_seconds, (int, float)) or not 0 <= termination_grace_seconds <= 30:
            raise ValueError("termination_grace_seconds must be between 0 and 30")
        for name, value, maximum in (
            ("max_request_bytes", max_request_bytes, 1_048_576),
            ("max_stdout_bytes", max_stdout_bytes, 1_048_576),
            ("max_stderr_bytes", max_stderr_bytes, 262_144),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} is outside its permitted range")
        mapping: dict[str, WorkerEntrypoint] = {}
        for item in entrypoints:
            if not isinstance(item, WorkerEntrypoint) or item.name in mapping:
                raise ValueError("entrypoints must have unique WorkerEntrypoint names")
            mapping[item.name] = item
        if not mapping:
            raise ValueError("at least one registered worker entrypoint is required")
        self._entrypoints = mapping
        needs_limits = any(item.resource_limits is not None for item in mapping.values())
        self._prlimit_path: str | None = None
        if needs_limits:
            if not sys.platform.startswith("linux"):
                raise WorkerSupervisorError("kernel resource limits currently require Linux prlimit(1)")
            helper = shutil.which("prlimit")
            if helper is None or not os.path.isabs(helper):
                raise WorkerSupervisorError("resource limits were requested but prlimit(1) is unavailable")
            self._prlimit_path = helper
        self.timeout_seconds = float(timeout_seconds)
        self.termination_grace_seconds = float(termination_grace_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes
        self._state_lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._active_processes: dict[int, subprocess.Popen[bytes]] = {}

    @property
    def shutting_down(self) -> bool:
        """Whether this supervisor has stopped accepting new children."""
        return self._shutdown_event.is_set()

    def shutdown(self, *, timeout_seconds: float = 5.0) -> bool:
        """Stop accepting children and terminate all active process groups.

        The timeout is a single global budget, not a per-child allowance.
        Returns true only when every registered direct child has been reaped.
        This method does not mutate leases or checkpoint state; callers must
        reconcile any in-flight side effects after shutdown.
        """
        if (isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not 0.0 <= timeout_seconds <= 60.0):
            raise ValueError("shutdown timeout_seconds must be between zero and 60")
        deadline = time.monotonic() + float(timeout_seconds)
        with self._state_lock:
            self._shutdown_event.set()
            processes = tuple(self._active_processes.values())
        for process in processes:
            if process.poll() is None:
                self._terminate_group(process, hard=False)
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            if process.poll() is None and remaining > 0:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    pass
        for process in processes:
            if process.poll() is None:
                self._terminate_group(process, hard=True)
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            if process.poll() is None and remaining > 0:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    pass
        return all(process.poll() is not None for process in processes)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes], *, hard: bool) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL if hard else signal.SIGTERM)
        except ProcessLookupError:
            pass

    def run(
        self, entrypoint: str, request: Mapping[str, Any], *,
        lease_session: WorkerLeaseSession | None = None,
    ) -> WorkerProcessResult:
        """Run a registered worker, optionally bound to a live durable lease session.

        The lease token is handed to the child only through an inherited private
        descriptor. Durable cancellation is signalled over a separate pipe. The
        child must return cancellation_acknowledged=true in its validated result
        to be classified as cooperatively cancelled. This method never
        acknowledges cancellation in the registry on the child's behalf.
        """
        if not isinstance(entrypoint, str) or entrypoint not in self._entrypoints:
            raise WorkerSupervisorError("worker entrypoint is not registered")
        config = self._entrypoints[entrypoint]
        try:
            Draft202012Validator(config.request_schema).validate(request)
        except Exception as exc:
            raise ValueError("worker request does not match its registered schema") from exc
        payload = _bounded_json(request, self.max_request_bytes)
        if lease_session is not None:
            if not isinstance(lease_session, WorkerLeaseSession):
                raise TypeError("lease_session must be a started WorkerLeaseSession")
            lease_session.assert_healthy()
            if lease_session.check_cancellation():
                raise WorkerSupervisorError("a durable cancellation is already pending; refusing child launch")
        started = time.monotonic()
        launch_argv = config.argv
        if config.resource_limits is not None:
            assert self._prlimit_path is not None
            limits = config.resource_limits
            launch_argv = (
                self._prlimit_path,
                f"--cpu={limits.cpu_seconds}",
                f"--as={limits.address_space_bytes}",
                f"--nofile={limits.open_files}",
                "--",
                *config.argv,
            )
        channel = create_worker_control_channel(lease_session.lease) if lease_session else None
        process: subprocess.Popen[bytes] | None = None
        try:
            environment = dict(config.environment or {})
            pass_fds: tuple[int, ...] = ()
            if channel is not None:
                control_fd, cancel_fd = channel.child_fds
                environment["SNGSO_WORKER_CONTROL_FD"] = str(control_fd)
                environment["SNGSO_WORKER_CANCEL_FD"] = str(cancel_fd)
                pass_fds = (control_fd, cancel_fd)
            with self._state_lock:
                if self._shutdown_event.is_set():
                    raise WorkerSupervisorError("supervisor is shutting down; refusing child launch")
                process = subprocess.Popen(
                    launch_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, shell=False, close_fds=True,
                    pass_fds=pass_fds, start_new_session=True, env=environment,
                )
                self._active_processes[process.pid] = process
            if channel is not None:
                channel.close_child_handles()
        except Exception:
            if channel is not None:
                channel.close()
            raise
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stdout = _BoundedDrain(process.stdout, self.max_stdout_bytes)
        stderr = _BoundedDrain(process.stderr, self.max_stderr_bytes)
        writer = _StdinWriter(process.stdin, payload)
        stdout.start()
        stderr.start()
        writer.start()
        termination = "none"
        status = "failed"
        cancellation_sent = False
        cancellation_deadline: float | None = None
        deadline = started + self.timeout_seconds
        try:
            while process.poll() is None:
                now = time.monotonic()
                if self._shutdown_event.is_set():
                    status = "shutdown"
                    termination = "sigterm"
                    self._terminate_group(process, hard=False)
                    try:
                        process.wait(timeout=self.termination_grace_seconds)
                    except subprocess.TimeoutExpired:
                        termination = "sigkill"
                        self._terminate_group(process, hard=True)
                        try:
                            process.wait(timeout=max(1.0, self.termination_grace_seconds))
                        except subprocess.TimeoutExpired:
                            termination = "kill_unconfirmed"
                    break
                if lease_session is not None:
                    if lease_session.heartbeat_error is not None:
                        status = "lease_lost"
                        termination = "sigterm"
                        self._terminate_group(process, hard=False)
                        try:
                            process.wait(timeout=self.termination_grace_seconds)
                        except subprocess.TimeoutExpired:
                            termination = "sigkill"
                            self._terminate_group(process, hard=True)
                            try:
                                process.wait(timeout=max(1.0, self.termination_grace_seconds))
                            except subprocess.TimeoutExpired:
                                termination = "kill_unconfirmed"
                        break
                    try:
                        cancellation_pending = (
                            not cancellation_sent and lease_session.check_cancellation()
                        )
                    except LeaseLostError:
                        status = "lease_lost"
                        termination = "sigterm"
                        self._terminate_group(process, hard=False)
                        try:
                            process.wait(timeout=self.termination_grace_seconds)
                        except subprocess.TimeoutExpired:
                            termination = "sigkill"
                            self._terminate_group(process, hard=True)
                            try:
                                process.wait(timeout=max(1.0, self.termination_grace_seconds))
                            except subprocess.TimeoutExpired:
                                termination = "kill_unconfirmed"
                        break
                    if cancellation_pending:
                        if channel is None:
                            status = "lease_lost"
                            termination = "control_channel_unavailable"
                            self._terminate_group(process, hard=True)
                            break
                        try:
                            channel.request_cancellation()
                        except Exception:
                            status = "failed"
                            termination = "cancellation_channel_unavailable"
                            self._terminate_group(process, hard=True)
                            break
                        cancellation_sent = True
                        cancellation_deadline = now + self.termination_grace_seconds
                    if (cancellation_sent and cancellation_deadline is not None
                            and now >= cancellation_deadline):
                        status = "cancelled_unacknowledged"
                        termination = "sigterm"
                        self._terminate_group(process, hard=False)
                        try:
                            process.wait(timeout=self.termination_grace_seconds)
                        except subprocess.TimeoutExpired:
                            termination = "sigkill"
                            self._terminate_group(process, hard=True)
                            try:
                                process.wait(timeout=max(1.0, self.termination_grace_seconds))
                            except subprocess.TimeoutExpired:
                                termination = "kill_unconfirmed"
                        break
                if now >= deadline:
                    status = "timeout"
                    termination = "sigterm"
                    self._terminate_group(process, hard=False)
                    try:
                        process.wait(timeout=self.termination_grace_seconds)
                    except subprocess.TimeoutExpired:
                        termination = "sigkill"
                        self._terminate_group(process, hard=True)
                        try:
                            process.wait(timeout=max(1.0, self.termination_grace_seconds))
                        except subprocess.TimeoutExpired:
                            termination = "kill_unconfirmed"
                    break
                try:
                    process.wait(timeout=min(0.05, max(0.001, deadline - now)))
                except subprocess.TimeoutExpired:
                    continue
            if process.poll() is not None and status == "failed":
                if self._shutdown_event.is_set():
                    status = "shutdown"
                    termination = "shutdown"
                else:
                    status = "succeeded" if process.returncode == 0 else "failed"
        finally:
            if channel is not None:
                channel.close()
            if process is not None:
                with self._state_lock:
                    self._active_processes.pop(process.pid, None)
        writer.join(timeout=1.0)
        stdout.join(timeout=1.0)
        stderr.join(timeout=1.0)
        if stdout.is_alive() or stderr.is_alive() or writer.is_alive():
            self._terminate_group(process, hard=True)
            writer.join(timeout=1.0)
            stdout.join(timeout=1.0)
            stderr.join(timeout=1.0)
            status = "failed"
            termination = "pipe_cleanup_unconfirmed"
        result: Mapping[str, Any] | None = None
        if status in {"succeeded", "cancelled_unacknowledged"}:
            if stdout.truncated or not stdout.data:
                status = "protocol_error" if status == "succeeded" else "cancelled_unacknowledged"
            else:
                try:
                    decoded = json.loads(bytes(stdout.data).decode("utf-8"))
                    if (not isinstance(decoded, dict) or set(decoded) != {"schema_version", "result"}
                            or decoded.get("schema_version") != "1"
                            or not isinstance(decoded.get("result"), dict)):
                        raise ValueError
                    candidate = dict(decoded["result"])
                    acknowledgement = candidate.pop("cancellation_acknowledged", False)
                    if not isinstance(acknowledgement, bool):
                        raise ValueError
                    validated = dict(candidate)
                    if "cancellation_acknowledged" in decoded["result"]:
                        validated["cancellation_acknowledged"] = acknowledgement
                    Draft202012Validator(config.result_schema).validate(validated)
                    if cancellation_sent:
                        status = "cancelled" if acknowledgement else "cancelled_unacknowledged"
                    elif acknowledgement:
                        raise ValueError
                    result = candidate
                except Exception:
                    status = "protocol_error" if status == "succeeded" else "cancelled_unacknowledged"
        return WorkerProcessResult(
            entrypoint=entrypoint, status=status, exit_code=process.poll(),
            result=result, duration_seconds=max(0.0, time.monotonic() - started),
            stdout_truncated=stdout.truncated, stderr_truncated=stderr.truncated,
            termination=termination,
        )


class WorkerProcessExecutionError(WorkerSupervisorError):
    """A supervised child did not produce a successful, validated stage result."""

    def __init__(self, result: WorkerProcessResult) -> None:
        self.process_result = result
        super().__init__(
            "supervised worker did not complete successfully "
            f"(status={result.status}, termination={result.termination})"
        )


def supervised_checkpoint_handler(
    supervisor: WorkerSupervisor,
    entrypoint: str,
    request_factory: Any,
    lease_session: WorkerLeaseSession,
):
    """Adapt a supervised child into a checkpoint-stage handler.

    The request factory receives the in-memory stage context and deterministic
    idempotency key. Only a validated successful child result becomes stage output.
    Any timeout, lease loss, cancellation, or protocol failure raises, allowing
    execute_checkpointed_stage to leave a non-replay-safe stage indeterminate.
    """
    if not isinstance(supervisor, WorkerSupervisor):
        raise TypeError("supervisor must be a WorkerSupervisor")
    if not isinstance(entrypoint, str) or entrypoint not in supervisor._entrypoints:
        raise WorkerSupervisorError("worker entrypoint is not registered")
    if not callable(request_factory):
        raise TypeError("request_factory must be callable")
    if not isinstance(lease_session, WorkerLeaseSession):
        raise TypeError("lease_session must be a WorkerLeaseSession")

    def handler(context: dict[str, Any], idempotency_key: str) -> Mapping[str, Any]:
        request = request_factory(context, idempotency_key)
        result = supervisor.run(entrypoint, request, lease_session=lease_session)
        if result.status != "succeeded" or result.result is None:
            raise WorkerProcessExecutionError(result)
        return dict(result.result)

    return handler
