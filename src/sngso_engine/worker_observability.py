"""Bounded, privacy-preserving worker process metrics.

This is an in-process telemetry primitive, not a monitoring backend. It stores
only registered entrypoint names and fixed outcome labels; it never records
request payloads, run IDs, worker IDs, credentials, stdout, or stderr.
"""
from __future__ import annotations

from collections import Counter, deque
import math
import re
import threading
from typing import Any, Iterable


_STATUSES = frozenset({
    "succeeded", "failed", "timeout", "lease_lost", "cancelled",
    "cancelled_unacknowledged", "shutdown", "protocol_error",
})
_TERMINATIONS = frozenset({
    "none", "sigterm", "sigkill", "kill_unconfirmed", "shutdown",
    "control_channel_unavailable", "cancellation_channel_unavailable",
    "pipe_cleanup_unconfirmed",
})
_DEFAULT_BUCKETS = (0.1, 0.5, 1.0, 5.0, 30.0, 120.0, 600.0)


class WorkerTelemetryError(ValueError):
    """Invalid worker telemetry configuration or observation."""


class WorkerTelemetry:
    """Thread-safe bounded counters, latency histograms, and recent outcomes."""

    def __init__(
        self,
        entrypoints: Iterable[str],
        *,
        max_events: int = 256,
        latency_buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    ) -> None:
        names = tuple(entrypoints)
        if not names or len(names) > 128 or len(set(names)) != len(names):
            raise WorkerTelemetryError("entrypoints must contain 1 to 128 unique names")
        if any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name)
               for name in names):
            raise WorkerTelemetryError("entrypoint names must be bounded lowercase identifiers")
        if (isinstance(max_events, bool) or not isinstance(max_events, int)
                or not 0 <= max_events <= 10_000):
            raise WorkerTelemetryError("max_events must be an integer from 0 to 10000")
        if (not isinstance(latency_buckets, tuple) or not latency_buckets
                or len(latency_buckets) > 32):
            raise WorkerTelemetryError("latency_buckets must be a non-empty tuple of at most 32 values")
        previous = 0.0
        for bucket in latency_buckets:
            if (isinstance(bucket, bool) or not isinstance(bucket, (int, float))
                    or not math.isfinite(bucket) or bucket <= previous):
                raise WorkerTelemetryError("latency buckets must be finite, positive, and strictly increasing")
            previous = float(bucket)
        self._entrypoints = frozenset(names)
        self._buckets = tuple(float(value) for value in latency_buckets)
        self._counts: Counter[tuple[str, str]] = Counter()
        self._latency: dict[str, list[int]] = {
            name: [0] * (len(self._buckets) + 1) for name in names
        }
        self._duration_sum: Counter[str] = Counter()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def accepts_entrypoint(self, name: str) -> bool:
        return name in self._entrypoints

    def record_result(self, result: Any) -> None:
        """Record a WorkerProcessResult-like object without retaining it."""
        try:
            entrypoint = result.entrypoint
            status = result.status
            termination = result.termination
            duration = result.duration_seconds
            exit_code = result.exit_code
        except Exception as exc:
            raise WorkerTelemetryError("worker result is missing required telemetry fields") from exc
        if entrypoint not in self._entrypoints:
            raise WorkerTelemetryError("worker result entrypoint is not registered for telemetry")
        if status not in _STATUSES or termination not in _TERMINATIONS:
            raise WorkerTelemetryError("worker result contains an unknown outcome label")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration < 0):
            raise WorkerTelemetryError("worker duration must be finite and non-negative")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise WorkerTelemetryError("worker exit code must be an integer or null")
        value = float(duration)
        bucket_index = next(
            (index for index, upper in enumerate(self._buckets) if value <= upper),
            len(self._buckets),
        )
        event = {
            "entrypoint": entrypoint,
            "status": status,
            "termination": termination,
            "exit_code": exit_code,
            "duration_seconds": value,
        }
        with self._lock:
            self._counts[(entrypoint, status)] += 1
            self._latency[entrypoint][bucket_index] += 1
            self._duration_sum[entrypoint] += value
            if self._events.maxlen:
                self._events.append(event)

    def snapshot(self) -> dict[str, Any]:
        """Return aggregate counters and bounded recent outcomes."""
        with self._lock:
            counts = {
                name: {
                    status: self._counts[(name, status)]
                    for status in sorted(_STATUSES)
                    if self._counts[(name, status)]
                }
                for name in sorted(self._entrypoints)
            }
            histograms = {
                name: {
                    "buckets_le_seconds": {
                        str(bound): count
                        for bound, count in zip(self._buckets, self._latency[name][:-1])
                    },
                    "buckets_gt_last": self._latency[name][-1],
                    "count": sum(self._latency[name]),
                    "duration_sum_seconds": self._duration_sum[name],
                }
                for name in sorted(self._entrypoints)
            }
            events = [dict(item) for item in self._events]
        return {
            "schema_version": "1.0.0",
            "scope": "in_process_worker_supervisor",
            "counts_by_entrypoint": counts,
            "latency": histograms,
            "recent_outcomes": events,
            "recent_outcomes_capacity": self._events.maxlen or 0,
            "production_backend_configured": False,
        }
