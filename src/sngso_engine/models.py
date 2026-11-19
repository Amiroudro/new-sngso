"""Immutable typed views of validated registry records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class _JsonRecord:
    id: str
    payload_json: str

    @classmethod
    def _from_payload(cls, payload: dict[str, Any]):
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
        )

    def to_payload(self) -> dict[str, Any]:
        import json

        return json.loads(self.payload_json)


@dataclass(frozen=True, slots=True)
class SourceRecord(_JsonRecord):
    """Validated publisher identity, not a permission grant."""

    publisher_name: str
    status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SourceRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            publisher_name=payload["publisher_name"],
            status=payload["status"],
        )


@dataclass(frozen=True, slots=True)
class SourceEndpointRecord(_JsonRecord):
    """A single feed, API route, sitemap, or page endpoint."""

    source_id: str
    endpoint_uri: str
    status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SourceEndpointRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            endpoint_uri=payload["endpoint_uri"],
            status=payload["status"],
        )


@dataclass(frozen=True, slots=True)
class PermissionEvidenceRecord(_JsonRecord):
    """Captured permission evidence and its review state."""

    source_id: str
    review_status: str
    content_fingerprint: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PermissionEvidenceRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            review_status=payload["review_status"],
            content_fingerprint=payload["content_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class PermissionGrantRecord(_JsonRecord):
    """A time-bounded grant scoped to exact endpoint and operation IDs."""

    source_id: str
    status: str
    effective_from: str
    effective_until: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PermissionGrantRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            status=payload["status"],
            effective_from=payload["effective_from"],
            effective_until=payload["effective_until"],
        )
