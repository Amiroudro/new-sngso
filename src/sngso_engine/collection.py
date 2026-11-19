"""Permission-first, fixture-compatible collection orchestration.

No network transport is implemented in this module. Connectors are invoked only
after the repository has approved the exact source/endpoint/operation tuple.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlsplit
import json
import uuid

from .permissions import OPERATIONS, PermissionDecision


_DISCOVERY_FIELDS = frozenset({"locator", "final_locator", "content_fingerprint"})
_METADATA_FIELDS = _DISCOVERY_FIELDS | frozenset(
    {"metadata", "response_status", "content_type"}
)
_METADATA_KEYS = frozenset({"title", "byline", "published_at", "summary_metadata"})


class CollectionError(ValueError):
    """Raised when a connector result violates the collection contract."""


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    source_id: str
    endpoint_id: str
    grant_id: str
    operation: str
    max_items: int = 20
    timeout_seconds: float = 10.0
    max_retries: int = 0
    max_observation_bytes: int = 65536

    def __post_init__(self) -> None:
        for field_name in ("source_id", "endpoint_id", "grant_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.operation, str) or self.operation not in OPERATIONS:
            raise ValueError("operation is not supported")
        if isinstance(self.max_items, bool) or not isinstance(self.max_items, int):
            raise ValueError("max_items must be an integer")
        if not 1 <= self.max_items <= 100:
            raise ValueError("max_items must be between 1 and 100")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0.1 <= self.timeout_seconds <= 30
        ):
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= 2
        ):
            raise ValueError("max_retries must be between 0 and 2")
        if (
            isinstance(self.max_observation_bytes, bool)
            or not isinstance(self.max_observation_bytes, int)
            or not 1024 <= self.max_observation_bytes <= 1_000_000
        ):
            raise ValueError("max_observation_bytes must be between 1024 and 1000000")


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    name: str
    version: str
    endpoint_types: frozenset[str]
    operations: frozenset[str]


@dataclass(frozen=True, slots=True)
class CollectionObservation:
    id: str
    source_id: str
    endpoint_id: str
    grant_id: str
    grant_revision: int | None
    operation: str
    connector_name: str
    connector_version: str
    observed_at: str
    locator: str
    final_locator: str | None
    content_fingerprint: str | None
    response_status: int | None
    content_type: str | None
    metadata: dict[str, str]
    permission_reason: str


@dataclass(frozen=True, slots=True)
class CollectionResult:
    allowed: bool
    reason: str
    observations: tuple[CollectionObservation, ...] = ()


class CollectionRepository(Protocol):
    def get_source(self, source_id: str) -> Any | None: ...
    def get_endpoint(self, endpoint_id: str) -> Any | None: ...
    def evaluate_grant(
        self, grant_id: str, operation: str, *, endpoint_id: str,
        now: datetime | None = None,
    ) -> PermissionDecision: ...
    def grant_history(self, grant_id: str) -> list[dict[str, Any]]: ...


class CollectionConnector(Protocol):
    @property
    def capabilities(self) -> ConnectorCapabilities: ...

    def collect(
        self, request: CollectionRequest, endpoint: Any
    ) -> Iterable[dict[str, Any]]:
        """Perform only the requested operation against the supplied endpoint."""
        ...


class ObservationSink(Protocol):
    def append_many(self, observations: tuple[CollectionObservation, ...]) -> None: ...


class DomainRateLimiter(Protocol):
    def acquire(self, domain: str) -> bool: ...


class InMemoryDomainRateLimiter:
    """Thread-safe fixed-window limiter suitable for fixture and local runs."""

    def __init__(
        self,
        *,
        max_requests: int = 10,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_requests < 1 or window_seconds <= 0:
            raise ValueError("rate-limit settings must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()

    def acquire(self, domain: str) -> bool:
        if not isinstance(domain, str) or not domain.strip():
            return False
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(domain.lower(), deque())
            while events and now - events[0] >= self.window_seconds:
                events.popleft()
            if len(events) >= self.max_requests:
                return False
            events.append(now)
            return True


class InMemoryObservationSink:
    """Deterministic append-only sink for tests and local fixtures only."""

    def __init__(self) -> None:
        self._observations: list[CollectionObservation] = []
        self._ids: set[str] = set()

    def append_many(self, observations: tuple[CollectionObservation, ...]) -> None:
        incoming = [item.id for item in observations]
        if len(incoming) != len(set(incoming)) or any(item in self._ids for item in incoming):
            raise CollectionError("observation IDs must be unique")
        self._observations.extend(observations)
        self._ids.update(incoming)

    def all(self) -> tuple[CollectionObservation, ...]:
        return tuple(self._observations)


class PermissionFirstCollector:
    """Authorize each exact operation before invoking its connector."""

    def __init__(
        self,
        repository: CollectionRepository,
        connectors: dict[str, CollectionConnector],
        observation_sink: ObservationSink,
        *,
        clock=lambda: datetime.now(timezone.utc),
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._repository = repository
        self._connectors = dict(connectors)
        self._sink = observation_sink
        self._clock = clock
        self._rate_limiter = rate_limiter or InMemoryDomainRateLimiter()

    def collect(self, request: CollectionRequest, connector_name: str) -> CollectionResult:
        if request.operation not in {"discovery", "metadata"}:
            return CollectionResult(False, "operation_not_implemented")
        connector = self._connectors.get(connector_name)
        if connector is None:
            return CollectionResult(False, "connector_not_registered")

        source = self._repository.get_source(request.source_id)
        if source is None:
            return CollectionResult(False, "source_record_missing")
        if source.status != "active":
            return CollectionResult(False, "source_not_active")

        endpoint = self._repository.get_endpoint(request.endpoint_id)
        if endpoint is None or endpoint.source_id != request.source_id:
            return CollectionResult(False, "endpoint_scope_mismatch")
        if endpoint.status != "active":
            return CollectionResult(False, "endpoint_not_active")

        endpoint_payload = endpoint.to_payload()
        capabilities = connector.capabilities
        endpoint_type = endpoint_payload.get("endpoint_type")
        if endpoint_type not in capabilities.endpoint_types:
            return CollectionResult(False, "connector_endpoint_type_unsupported")
        if request.operation not in capabilities.operations:
            return CollectionResult(False, "connector_operation_unsupported")

        domain = urlsplit(endpoint.endpoint_uri).hostname
        if not domain:
            return CollectionResult(False, "invalid_endpoint_uri")

        evaluated_at = self._clock()
        decision = self._repository.evaluate_grant(
            request.grant_id,
            request.operation,
            endpoint_id=request.endpoint_id,
            now=evaluated_at,
        )
        if not decision.allowed:
            return CollectionResult(False, decision.reason)
        if not self._rate_limiter.acquire(domain):
            return CollectionResult(False, "rate_limited")

        # Capture the authorized revision before connector execution. The
        # persistent sink checks it again so revocation during collection
        # prevents the returned observations from being stored.
        grant_revision = self._latest_grant_revision(request.grant_id)

        # This is the first point at which connector code is invoked.
        raw_items = connector.collect(request, endpoint)
        validated: list[CollectionObservation] = []
        total_observation_bytes = 0
        for index, raw in enumerate(raw_items):
            if index >= request.max_items:
                raise CollectionError("connector exceeded max_items")
            try:
                total_observation_bytes += len(
                    json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                )
            except (TypeError, ValueError) as exc:
                raise CollectionError("connector returned a non-JSON observation") from exc
            if total_observation_bytes > request.max_observation_bytes:
                raise CollectionError("connector exceeded max_observation_bytes")
            validated.append(
                self._validate_observation(
                    raw,
                    request=request,
                    capabilities=capabilities,
                    permission_reason=decision.reason,
                    grant_revision=grant_revision,
                )
            )

        observations = tuple(validated)
        self._sink.append_many(observations)
        return CollectionResult(True, "approved", observations)

    def _latest_grant_revision(self, grant_id: str) -> int | None:
        history = self._repository.grant_history(grant_id)
        if not history:
            return None
        return int(history[-1]["revision"])

    def _validate_observation(
        self,
        raw: dict[str, Any],
        *,
        request: CollectionRequest,
        capabilities: ConnectorCapabilities,
        permission_reason: str,
        grant_revision: int | None,
    ) -> CollectionObservation:
        if not isinstance(raw, dict):
            raise CollectionError("connector observations must be objects")
        allowed_fields = (
            _DISCOVERY_FIELDS
            if request.operation == "discovery"
            else _METADATA_FIELDS
        )
        if set(raw) - allowed_fields:
            raise CollectionError("connector returned fields outside the requested operation")

        locator = raw.get("locator")
        if not _is_http_uri(locator):
            raise CollectionError("observation locator must be an absolute HTTP(S) URI")
        final_locator = raw.get("final_locator")
        if final_locator is not None and not _is_http_uri(final_locator):
            raise CollectionError("final_locator must be an absolute HTTP(S) URI")

        fingerprint = raw.get("content_fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or not fingerprint.startswith("sha256:")
            or len(fingerprint) != 71
        ):
            raise CollectionError("content_fingerprint must be a SHA-256 fingerprint")

        response_status = raw.get("response_status")
        if response_status is not None and (
            isinstance(response_status, bool)
            or not isinstance(response_status, int)
            or not 100 <= response_status <= 599
        ):
            raise CollectionError("response_status must be a valid HTTP status")

        content_type = raw.get("content_type")
        if content_type is not None and (
            not isinstance(content_type, str) or len(content_type) > 160
        ):
            raise CollectionError("content_type must be a short string")

        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict) or set(metadata) - _METADATA_KEYS:
            raise CollectionError("metadata contains unsupported fields")
        clean_metadata: dict[str, str] = {}
        for key, value in metadata.items():
            if not isinstance(value, str) or len(value) > 1000:
                raise CollectionError(f"metadata field {key} must be a string of at most 1000 characters")
            clean_metadata[key] = value

        if request.operation == "discovery" and metadata:
            raise CollectionError("discovery cannot retain article metadata")

        return CollectionObservation(
            id=f"observation:{uuid.uuid4().hex}",
            source_id=request.source_id,
            endpoint_id=request.endpoint_id,
            grant_id=request.grant_id,
            grant_revision=grant_revision,
            operation=request.operation,
            connector_name=capabilities.name,
            connector_version=capabilities.version,
            observed_at=self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            locator=locator,
            final_locator=final_locator,
            content_fingerprint=fingerprint,
            response_status=response_status,
            content_type=content_type,
            metadata=clean_metadata,
            permission_reason=permission_reason,
        )


def _is_http_uri(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False
