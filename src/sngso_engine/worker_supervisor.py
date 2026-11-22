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
import signal
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


class WorkerSupervisorError(RuntimeError):
    """Base error for invalid supervisor configuration or requests."""


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

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", self.name):
            raise ValueError("entrypoint name must be a bounded lowercase identifier")
        if not isinstance(self.argv, tuple) or not self.argv or len(self.argv) > 32:
            raise ValueError("entrypoint argv must be a non-empty tuple of at most 32 arguments")
        for item in self.argv:
            if not isinstance(item, str) or not item or "\x00" in item or len(item) > 4096:
                raise ValueError("entrypoint argv contains an invalid argument")
        for schema in (self.request_schema, self.result_schema):
            if not isinstance(schema, Mapping):
                raise ValueError("worker request and result schemas must be JSON Schema objects")
            Draft202012Validator.check_schema(schema)
        if self.environment is not None:
            if not isinstance(self.environment, Mapping) or len(self.environment) > 32:
                raise ValueError("entrypoint environment must be a bounded mapping")
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
        self.timeout_seconds = float(timeout_seconds)
        self.termination_grace_seconds = float(termination_grace_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes], *, hard: bool) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL if hard else signal.SIGTERM)
        except ProcessLookupError:
            pass

    def run(self, entrypoint: str, request: Mapping[str, Any]) -> WorkerProcessResult:
        """Run a registered worker. Request data is JSON on stdin, never argv."""
        if not isinstance(entrypoint, str) or entrypoint not in self._entrypoints:
            raise WorkerSupervisorError("worker entrypoint is not registered")
        config = self._entrypoints[entrypoint]
        try:
            Draft202012Validator(config.request_schema).validate(request)
        except Exception as exc:
            raise ValueError("worker request does not match its registered schema") from exc
        payload = _bounded_json(request, self.max_request_bytes)
        started = time.monotonic()
        process = subprocess.Popen(
            config.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
            env=dict(config.environment or {}),
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stdout = _BoundedDrain(process.stdout, self.max_stdout_bytes)
        stderr = _BoundedDrain(process.stderr, self.max_stderr_bytes)
        writer = _StdinWriter(process.stdin, payload)
        stdout.start()
        stderr.start()
        writer.start()
        termination = "none"
        status = "failed"
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
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
                    # OS process state is outside Python's control; report the failure.
                    termination = "kill_unconfirmed"
        if process.poll() is not None:
            status = "succeeded" if status != "timeout" and process.returncode == 0 else status
        if writer.error is not None and status == "succeeded":
            status = "protocol_error"
        writer.join(timeout=1.0)
        stdout.join(timeout=1.0)
        stderr.join(timeout=1.0)
        # A surviving drain thread means a descendant retained a pipe. Terminate
        # the process group and fail closed rather than waiting without a bound.
        if stdout.is_alive() or stderr.is_alive() or writer.is_alive():
            self._terminate_group(process, hard=True)
            writer.join(timeout=1.0)
            stdout.join(timeout=1.0)
            stderr.join(timeout=1.0)
            status = "failed"
            termination = "pipe_cleanup_unconfirmed"
        result: Mapping[str, Any] | None = None
        if status == "succeeded":
            if stdout.truncated or not stdout.data:
                status = "protocol_error"
            else:
                try:
                    decoded = json.loads(bytes(stdout.data).decode("utf-8"))
                    if (not isinstance(decoded, dict) or set(decoded) != {"schema_version", "result"}
                            or decoded.get("schema_version") != "1"
                            or not isinstance(decoded.get("result"), dict)):
                        raise ValueError
                    Draft202012Validator(config.result_schema).validate(decoded["result"])
                    result = decoded["result"]
                except Exception:
                    status = "protocol_error"
        return WorkerProcessResult(
            entrypoint=entrypoint,
            status=status,
            exit_code=process.poll(),
            result=result,
            duration_seconds=max(0.0, time.monotonic() - started),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            termination=termination,
        )
