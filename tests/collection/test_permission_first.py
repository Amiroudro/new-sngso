from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sngso_engine.collection import (
    CollectionCapabilities,
    CollectionError,
    CollectionRequest,
    ConnectorCapabilities,
    InMemoryObservationSink,
    PermissionFirstCollector,
)
from sngso_engine.registry import SQLiteRegistry


STAMP = "2026-11-19T00:00:00Z"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def source_payload(source_id: str = "source:publisher-a") -> dict:
    name = source_id.split(":", 1)[1]
    return {
        "schema_version": "1.0.0",
        "id": source_id,
        "publisher_name": f"Publisher {name}",
        "publisher_type": "newspaper",
        "canonical_homepage": f"https://{name}.example.org/",
        "domains": [f"{name}.example.org"],
        "country_code": "BD",
        "languages": ["bn", "en"],
        "status": "active",
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def endpoint_payload(
    endpoint_id: str = "endpoint:publisher-a-rss",
    source_id: str = "source:publisher-a",
) -> dict:
    name = source_id.split(":", 1)[1]
    return {
        "schema_version": "1.0.0",
        "id": endpoint_id,
        "source_id": source_id,
        "endpoint_uri": f"https://{name}.example.org/feed.xml",
        "endpoint_type": "rss",
        "status": "active",
        "supported_languages": ["bn"],
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def evidence_payload() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "permission-evidence:publisher-a-001",
        "source_id": "source:publisher-a",
        "evidence_type": "written_grant",
        "evidence_uri": "https://publisher-a.example.org/permissions/letter",
        "captured_at": STAMP,
        "content_fingerprint": "sha256:" + "a" * 64,
        "scope_description": "Permission for the named RSS endpoint and approved operations.",
        "review_status": "verified",
        "reviewed_at": STAMP,
        "reviewed_by": "rights-reviewer",
    }


def grant_payload(allowed_operations=("discovery",)) -> dict:
    evidence_id = "permission-evidence:publisher-a-001"
    operations = {}
    for operation in (
        "discovery",
        "metadata",
        "excerpt_storage",
        "full_text_storage",
        "translation",
        "image_storage",
        "republication",
    ):
        allowed = operation in allowed_operations
        operations[operation] = {
            "allowed": allowed,
            "status": "approved" if allowed else "denied",
            "conditions": [],
            **({"evidence_ids": [evidence_id]} if allowed else {}),
        }
    return {
        "schema_version": "1.0.0",
        "id": "permission:publisher-a-grant",
        "source_id": "source:publisher-a",
        "endpoint_ids": ["endpoint:publisher-a-rss"],
        "status": "approved",
        "operations": operations,
        "evidence_ids": [evidence_id],
        "basis": "Fixture grant for deterministic tests only.",
        "effective_from": "2026-11-18T00:00:00Z",
        "effective_until": "2026-12-18T00:00:00Z",
        "created_at": STAMP,
        "reviewed_at": STAMP,
        "reviewed_by": "rights-reviewer",
    }


class FixtureConnector:
    def __init__(self, items, operations=("discovery", "metadata"), endpoint_types=("rss",)):
        self.items = items
        self.calls = 0
        self.capabilities = ConnectorCapabilities(
            name="fixture-feed",
            version="1.0.0",
            endpoint_types=frozenset(endpoint_types),
            operations=frozenset(operations),
        )

    def collect(self, request, endpoint):
        self.calls += 1
        return iter(self.items)


def populated_registry(allowed_operations=("discovery",)):
    registry = SQLiteRegistry()
    registry.add_source(source_payload())
    registry.add_endpoint(endpoint_payload())
    registry.add_permission_evidence(evidence_payload())
    registry.add_permission_grant(grant_payload(allowed_operations))
    return registry


def make_request(operation="discovery", **overrides):
    values = {
        "source_id": "source:publisher-a",
        "endpoint_id": "endpoint:publisher-a-rss",
        "grant_id": "permission:publisher-a-grant",
        "operation": operation,
    }
    values.update(overrides)
    return CollectionRequest(**values)


def make_collector(registry, connector, sink):
    return PermissionFirstCollector(
        registry,
        {"fixture-feed": connector},
        sink,
        clock=lambda: NOW,
    )


def test_denied_operation_never_invokes_connector() -> None:
    with populated_registry(("discovery",)) as registry:
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/a"}])
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request("metadata"), "fixture-feed"
        )

        assert (result.allowed, result.reason) == (False, "operation_not_approved")
        assert connector.calls == 0
        assert sink.all() == ()


def test_approved_discovery_records_provenance_and_locator_only() -> None:
    with populated_registry(("discovery",)) as registry:
        connector = FixtureConnector(
            [{"locator": "https://publisher-a.example.org/news/1", "content_fingerprint": "sha256:" + "b" * 64}]
        )
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request(), "fixture-feed"
        )

        assert result.allowed is True
        assert connector.calls == 1
        assert len(result.observations) == 1
        observation = result.observations[0]
        assert observation.source_id == "source:publisher-a"
        assert observation.endpoint_id == "endpoint:publisher-a-rss"
        assert observation.grant_id == "permission:publisher-a-grant"
        assert observation.grant_revision == 1
        assert observation.operation == "discovery"
        assert observation.metadata == {}
        assert sink.all() == result.observations


def test_metadata_requires_its_own_approval_and_is_bounded() -> None:
    with populated_registry(("metadata",)) as registry:
        connector = FixtureConnector(
            [{
                "locator": "https://publisher-a.example.org/news/1",
                "metadata": {"title": "A verified headline", "byline": "Staff Reporter"},
                "response_status": 200,
                "content_type": "application/rss+xml",
            }]
        )
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request("metadata"), "fixture-feed"
        )

        assert result.allowed is True
        assert result.observations[0].metadata == {
            "title": "A verified headline",
            "byline": "Staff Reporter",
        }


def test_full_text_collection_is_not_implemented_even_if_grant_allows_it() -> None:
    with populated_registry(("full_text_storage",)) as registry:
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/a"}])
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request("full_text_storage"), "fixture-feed"
        )

        assert (result.allowed, result.reason) == (False, "operation_not_implemented")
        assert connector.calls == 0
        assert sink.all() == ()


def test_endpoint_from_another_source_is_rejected_before_connector() -> None:
    with populated_registry() as registry:
        registry.add_source(source_payload("source:publisher-b"))
        registry.add_endpoint(endpoint_payload("endpoint:publisher-b-rss", "source:publisher-b"))
        connector = FixtureConnector([{"locator": "https://publisher-b.example.org/a"}])
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request(endpoint_id="endpoint:publisher-b-rss"), "fixture-feed"
        )

        assert (result.allowed, result.reason) == (False, "endpoint_scope_mismatch")
        assert connector.calls == 0


def test_unsupported_connector_capability_is_rejected_before_connector() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector(
            [{"locator": "https://publisher-a.example.org/a"}],
            endpoint_types=("api",),
        )
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request(), "fixture-feed"
        )

        assert (result.allowed, result.reason) == (
            False,
            "connector_endpoint_type_unsupported",
        )
        assert connector.calls == 0


def test_discovery_connector_cannot_return_metadata_or_full_text() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{
            "locator": "https://publisher-a.example.org/a",
            "metadata": {"title": "Not allowed during discovery"},
        }])
        sink = InMemoryObservationSink()
        collector = make_collector(registry, connector, sink)

        with pytest.raises(CollectionError, match="outside the requested operation"):
            collector.collect(make_request(), "fixture-feed")
        assert sink.all() == ()


def test_connector_output_is_bounded_by_max_items() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([
            {"locator": "https://publisher-a.example.org/1"},
            {"locator": "https://publisher-a.example.org/2"},
        ])
        sink = InMemoryObservationSink()

        with pytest.raises(CollectionError, match="exceeded max_items"):
            make_collector(registry, connector, sink).collect(
                make_request(max_items=1), "fixture-feed"
            )
        assert sink.all() == ()


def test_invalid_request_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_items"):
        make_request(max_items=101)
