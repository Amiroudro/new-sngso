from __future__ import annotations

from datetime import datetime, timezone
import json
from dataclasses import replace
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from sngso_engine import collection as collection_module
from sngso_engine.collection import (
    CollectionError,
    CollectionExecutionContext,
    CollectionRequest,
    ConnectorCapabilities,
    InMemoryDomainRateLimiter,
    InMemoryObservationSink,
    PermissionFirstCollector,
)
from sngso_engine.contracts import ContractValidationError
from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry


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

    def collect(self, request, endpoint, context):
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


def make_collector(registry, connector, sink, rate_limiter=None):
    return PermissionFirstCollector(
        registry,
        {"fixture-feed": connector},
        sink,
        clock=lambda: NOW,
        rate_limiter=rate_limiter,
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
        sink = registry
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
        stored = registry.collection_observations()
        assert len(stored) == 1
        assert stored[0]["id"] == observation.id
        assert stored[0]["grant_revision"] == 1
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute("DELETE FROM collection_observations")
        assert registry.verify_audit_chain()


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
        sink = registry
        result = make_collector(registry, connector, sink).collect(
            make_request("metadata"), "fixture-feed"
        )

        assert result.allowed is True
        assert result.observations[0].metadata == {
            "title": "A verified headline",
            "byline": "Staff Reporter",
        }
        assert registry.collection_observations()[0]["metadata"]["title"] == "A verified headline"


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


def test_connector_fields_are_rejected_before_json_serialization():
    with populated_registry() as registry:
        connector = FixtureConnector([{
            "locator": "https://publisher-a.example.org/news/1",
            "unapproved_payload": object(),
        }])
        sink = InMemoryObservationSink()

        with pytest.raises(CollectionError, match="outside the requested operation"):
            make_collector(registry, connector, sink).collect(
                make_request(), "fixture-feed"
            )
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

def test_revocation_during_connector_execution_prevents_observation_storage() -> None:
    with populated_registry() as registry:
        class RevokingConnector(FixtureConnector):
            def collect(self, request, endpoint, context):
                self.calls += 1
                registry.transition_permission_grant(
                    request.grant_id,
                    "revoked",
                    actor="rights-reviewer",
                    reason="Permission withdrawn during fixture collection.",
                )
                return iter([{"locator": "https://publisher-a.example.org/news/1"}])

        connector = RevokingConnector([{"locator": "https://publisher-a.example.org/news/1"}])
        collector = make_collector(registry, connector, registry)

        with pytest.raises(RegistryIntegrityError, match="grant changed during collection"):
            collector.collect(make_request(), "fixture-feed")

        assert connector.calls == 1
        assert registry.collection_observations() == []

def _persistent_registry_pair(tmp_path):
    database = tmp_path / "permission-race.sqlite"
    first = SQLiteRegistry(database)
    first.add_source(source_payload())
    first.add_endpoint(endpoint_payload())
    first.add_permission_evidence(evidence_payload())
    first.add_permission_grant(grant_payload())
    second = SQLiteRegistry(database)

    connector = FixtureConnector([{
        "locator": "https://publisher-a.example.org/news/race"
    }])
    result = make_collector(
        first, connector, InMemoryObservationSink()
    ).collect(make_request(), "fixture-feed")
    assert result.allowed is True
    return first, second, result.observations


def test_cross_connection_revocation_wins_before_observation_storage(tmp_path, monkeypatch):
    first, second, observations = _persistent_registry_pair(tmp_path)
    revision_inserted = Event()
    allow_revoke_commit = Event()
    original_insert = second._insert_grant_revision

    def pause_before_revocation_commit(*args, **kwargs):
        revision_inserted.set()
        assert allow_revoke_commit.wait(5)
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(second, "_insert_grant_revision", pause_before_revocation_commit)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            revoke = executor.submit(
                second.transition_permission_grant,
                "permission:publisher-a-grant",
                "revoked",
                actor="rights-reviewer",
                reason="Concurrent withdrawal test.",
            )
            assert revision_inserted.wait(5)
            store_started = Event()

            def store():
                store_started.set()
                first.append_many(observations)

            store = executor.submit(store)
            assert store_started.wait(5)
            allow_revoke_commit.set()
            revoke.result(timeout=5)
            with pytest.raises(RegistryIntegrityError, match="grant changed during collection"):
                store.result(timeout=5)

        assert first.collection_observations() == []
        assert first.get_permission_grant("permission:publisher-a-grant").status == "revoked"
        assert first.verify_audit_chain()
    finally:
        allow_revoke_commit.set()
        first.close()
        second.close()


def test_cross_connection_observation_storage_finishes_before_revocation(tmp_path, monkeypatch):
    first, second, observations = _persistent_registry_pair(tmp_path)
    observation_inserted = Event()
    allow_storage_commit = Event()
    revoke_started = Event()
    original_audit = first._append_audit

    def pause_after_observation_insert(connection, **kwargs):
        if kwargs.get("event_type") == "collection.observation_recorded":
            observation_inserted.set()
            assert allow_storage_commit.wait(5)
        return original_audit(connection, **kwargs)

    monkeypatch.setattr(first, "_append_audit", pause_after_observation_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            store = executor.submit(first.append_many, observations)
            assert observation_inserted.wait(5)

            def revoke():
                revoke_started.set()
                return second.transition_permission_grant(
                    "permission:publisher-a-grant",
                    "revoked",
                    actor="rights-reviewer",
                    reason="Concurrent withdrawal after storage began.",
                )

            revoke_future = executor.submit(revoke)
            assert revoke_started.wait(5)
            allow_storage_commit.set()
            store.result(timeout=5)
            revoke_future.result(timeout=5)

        assert len(first.collection_observations()) == 1
        assert first.get_permission_grant("permission:publisher-a-grant").status == "revoked"
        assert first.verify_audit_chain()
    finally:
        allow_storage_commit.set()
        first.close()
        second.close()


def test_per_domain_rate_limit_stops_connector_before_second_call() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/news/1"}])
        sink = InMemoryObservationSink()
        limiter = InMemoryDomainRateLimiter(max_requests=1, window_seconds=60, clock=lambda: 100.0)
        collector = make_collector(registry, connector, sink, limiter)

        first = collector.collect(make_request(), "fixture-feed")
        second = collector.collect(make_request(), "fixture-feed")

        assert first.allowed is True
        assert (second.allowed, second.reason) == (False, "rate_limited")
        assert connector.calls == 1


def test_observation_byte_budget_is_enforced_before_persistence() -> None:
    with populated_registry() as registry:
        long_locator = "https://publisher-a.example.org/" + "x" * 1500
        connector = FixtureConnector([{"locator": long_locator}])
        sink = InMemoryObservationSink()

        with pytest.raises(CollectionError, match="max_observation_bytes"):
            make_collector(registry, connector, sink).collect(
                make_request(max_observation_bytes=1024), "fixture-feed"
            )
        assert sink.all() == ()


def test_timeout_and_retry_budgets_have_hard_bounds() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        make_request(timeout_seconds=60)
    with pytest.raises(ValueError, match="max_retries"):
        make_request(max_retries=3)

def test_persistence_boundary_rejects_unapproved_content_fields() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/news/1"}])
        temporary_sink = InMemoryObservationSink()
        result = make_collector(registry, connector, temporary_sink).collect(
            make_request(), "fixture-feed"
        )
        forged = replace(result.observations[0], metadata={"full_text": "article body"})

        with pytest.raises(RegistryIntegrityError, match="unsupported fields"):
            registry.append_many((forged,))

        assert registry.collection_observations() == []

def test_permission_is_rechecked_after_rate_limit_before_connector_call() -> None:
    with populated_registry() as registry:
        class RevokingLimiter:
            def acquire(self, domain):
                registry.transition_permission_grant(
                    "permission:publisher-a-grant",
                    "revoked",
                    actor="rights-reviewer",
                    reason="Permission withdrawn before connector invocation.",
                )
                return True

        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/news/1"}])
        sink = InMemoryObservationSink()
        collector = make_collector(registry, connector, sink, RevokingLimiter())
        result = collector.collect(make_request(), "fixture-feed")

        assert (result.allowed, result.reason) == (False, "permission_not_approved")
        assert connector.calls == 0
        assert sink.all() == ()


def test_observation_locator_outside_registered_source_domains_is_rejected() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{
            "locator": "https://unrelated.example.net/story/1"
        }])
        sink = InMemoryObservationSink()

        with pytest.raises(CollectionError, match="outside the registered source domains"):
            make_collector(registry, connector, sink).collect(
                make_request(), "fixture-feed"
            )
        assert connector.calls == 1
        assert sink.all() == ()


def test_final_locator_cannot_escape_registered_source_domains() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{
            "locator": "https://publisher-a.example.org/story/1",
            "final_locator": "https://unrelated.example.net/story/1",
        }])
        sink = InMemoryObservationSink()

        with pytest.raises(CollectionError, match="final_locator is outside the registered source domains"):
            make_collector(registry, connector, sink).collect(
                make_request(), "fixture-feed"
            )
        assert sink.all() == ()


def test_registered_source_domain_allows_its_subdomains() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{
            "locator": "https://news.publisher-a.example.org/story/1"
        }])
        result = make_collector(registry, connector, registry).collect(
            make_request(), "fixture-feed"
        )
        assert result.allowed is True


def test_endpoint_host_outside_registered_source_domains_is_rejected() -> None:
    with populated_registry() as registry:
        registry.add_endpoint({
            **endpoint_payload("endpoint:publisher-a-external"),
            "endpoint_uri": "https://unrelated.example.net/feed.xml",
        })
        connector = FixtureConnector([{
            "locator": "https://unrelated.example.net/story/1"
        }])
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request(endpoint_id="endpoint:publisher-a-external"), "fixture-feed"
        )
        assert (result.allowed, result.reason) == (
            False, "endpoint_domain_outside_source_scope"
        )
        assert connector.calls == 0



def test_registry_rejects_out_of_scope_locator_even_when_collector_is_bypassed() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([{
            "locator": "https://publisher-a.example.org/story/1"
        }])
        result = make_collector(registry, connector, InMemoryObservationSink()).collect(
            make_request(), "fixture-feed"
        )
        forged = replace(
            result.observations[0],
            locator="https://unrelated.example.net/story/1",
        )

        with pytest.raises(RegistryIntegrityError, match="outside the registered source domains"):
            registry.append_many((forged,))
        assert registry.collection_observations() == []


def test_inactive_source_is_rejected_before_connector_invocation() -> None:
    with populated_registry() as registry:
        row = registry._connection.execute(
            "SELECT payload_json FROM sources WHERE id = ?",
            ("source:publisher-a",),
        ).fetchone()
        import json
        payload = json.loads(row["payload_json"])
        payload["status"] = "paused"
        registry._connection.execute(
            "UPDATE sources SET status = ?, payload_json = ? WHERE id = ?",
            ("paused", json.dumps(payload), "source:publisher-a"),
        )
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/story/1"}])
        result = make_collector(registry, connector, InMemoryObservationSink()).collect(
            make_request(), "fixture-feed"
        )
        assert (result.allowed, result.reason) == (False, "source_not_active")
        assert connector.calls == 0


def test_inactive_endpoint_is_rejected_before_connector_invocation() -> None:
    with populated_registry() as registry:
        row = registry._connection.execute(
            "SELECT payload_json FROM source_endpoints WHERE id = ?",
            ("endpoint:publisher-a-rss",),
        ).fetchone()
        import json
        payload = json.loads(row["payload_json"])
        payload["status"] = "paused"
        registry._connection.execute(
            "UPDATE source_endpoints SET status = ?, payload_json = ? WHERE id = ?",
            ("paused", json.dumps(payload), "endpoint:publisher-a-rss"),
        )
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/story/1"}])
        result = make_collector(registry, connector, InMemoryObservationSink()).collect(
            make_request(), "fixture-feed"
        )
        assert (result.allowed, result.reason) == (False, "endpoint_not_active")
        assert connector.calls == 0


def test_expired_grant_is_rejected_before_connector_invocation() -> None:
    expired = grant_payload()
    expired["effective_until"] = "2026-11-19T23:59:59Z"
    registry = SQLiteRegistry()
    registry.add_source(source_payload())
    registry.add_endpoint(endpoint_payload())
    registry.add_permission_evidence(evidence_payload())
    registry.add_permission_grant(expired)
    try:
        connector = FixtureConnector([{"locator": "https://publisher-a.example.org/story/1"}])
        result = make_collector(registry, connector, InMemoryObservationSink()).collect(
            make_request(), "fixture-feed"
        )
        assert (result.allowed, result.reason) == (False, "permission_expired")
        assert connector.calls == 0
    finally:
        registry.close()


def test_metadata_rejects_unapproved_fields() -> None:
    with populated_registry(("metadata",)) as registry:
        connector = FixtureConnector([{
            "locator": "https://publisher-a.example.org/story/1",
            "metadata": {"full_text": "This must never be retained here."},
        }])
        with pytest.raises(CollectionError, match="metadata contains unsupported fields"):
            make_collector(registry, connector, InMemoryObservationSink()).collect(
                make_request("metadata"), "fixture-feed"
            )


def test_observation_batch_is_atomic_when_later_insert_fails() -> None:
    with populated_registry() as registry:
        connector = FixtureConnector([
            {"locator": "https://publisher-a.example.org/story/1"},
            {"locator": "https://publisher-a.example.org/story/2"},
        ])
        result = make_collector(registry, connector, InMemoryObservationSink()).collect(
            make_request(), "fixture-feed"
        )
        first, second = result.observations
        with pytest.raises(sqlite3.IntegrityError):
            registry.append_many((first, first))
        assert registry.collection_observations() == []
        assert registry.verify_audit_chain()



class FakeTransport:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def fetch(self, url, *, allowed_domains, timeout_seconds, max_bytes, max_retries):
        self.calls.append({
            "url": url,
            "allowed_domains": allowed_domains,
            "timeout_seconds": timeout_seconds,
            "max_bytes": max_bytes,
            "max_retries": max_retries,
        })
        if self.error is not None:
            raise self.error
        class Response:
            status = 200
            headers = {"content-type": "application/rss+xml"}
            final_url = url
        return Response()


class TransportBackedConnector(FixtureConnector):
    def __init__(self, transport, *, after_fetch=None):
        super().__init__([])
        self.transport = transport
        self.after_fetch = after_fetch
        self.capabilities = ConnectorCapabilities(
            name="transport-fixture",
            version="1.0.0",
            endpoint_types=frozenset({"rss"}),
            operations=frozenset({"discovery", "metadata"}),
        )

    def collect(self, request, endpoint, context):
        self.calls += 1
        response = self.transport.fetch(
            endpoint.endpoint_uri,
            allowed_domains=context.allowed_domains,
            timeout_seconds=request.timeout_seconds,
            max_bytes=request.max_observation_bytes,
            max_retries=request.max_retries,
        )
        if self.after_fetch is not None:
            self.after_fetch()
        return iter([{
            "locator": endpoint.endpoint_uri,
            "final_locator": response.final_url,
        }])


def test_denied_permission_never_reaches_transport() -> None:
    with populated_registry(("discovery",)) as registry:
        transport = FakeTransport()
        connector = TransportBackedConnector(transport)
        result = make_collector(
            registry, connector, registry
        ).collect(make_request("metadata"), "fixture-feed")

        assert (result.allowed, result.reason) == (False, "operation_not_approved")
        assert connector.calls == 0
        assert transport.calls == []
        assert registry.collection_observations() == []


def test_authorized_connector_receives_exact_registered_domain_scope() -> None:
    with populated_registry(("discovery",)) as registry:
        transport = FakeTransport()
        connector = TransportBackedConnector(transport)
        result = make_collector(
            registry, connector, registry
        ).collect(make_request(), "fixture-feed")

        assert result.allowed is True
        assert len(transport.calls) == 1
        assert transport.calls[0]["url"] == "https://publisher-a.example.org/feed.xml"
        assert transport.calls[0]["allowed_domains"] == ("publisher-a.example.org",)
        assert len(registry.collection_observations()) == 1


def test_transport_failure_creates_no_partial_observation() -> None:
    with populated_registry() as registry:
        transport = FakeTransport(error=RuntimeError("simulated network failure"))
        connector = TransportBackedConnector(transport)

        with pytest.raises(RuntimeError, match="simulated network failure"):
            make_collector(
                registry, connector, registry
            ).collect(make_request(), "fixture-feed")

        assert connector.calls == 1
        assert len(transport.calls) == 1
        assert registry.collection_observations() == []
        assert registry.verify_audit_chain()


def test_permission_revoked_after_transport_response_blocks_storage() -> None:
    with populated_registry() as registry:
        transport = FakeTransport()

        def revoke():
            registry.transition_permission_grant(
                "permission:publisher-a-grant",
                "revoked",
                actor="rights-reviewer",
                reason="Permission withdrawn after transport returned.",
            )

        connector = TransportBackedConnector(transport, after_fetch=revoke)
        with pytest.raises(RegistryIntegrityError, match="grant changed during collection"):
            make_collector(
                registry, connector, registry
            ).collect(make_request(), "fixture-feed")

        assert len(transport.calls) == 1
        assert registry.collection_observations() == []



def test_domain_scope_and_rate_limit_identity_use_canonical_dns_names():
    assert collection_module._canonical_dns_name("News.Publisher-A.Example.Org.") == (
        "news.publisher-a.example.org"
    )
    assert collection_module._canonical_dns_name("news.publisher-a.example.org..") is None
    assert collection_module._host_in_domain_allowlist(
        "News.Publisher-A.Example.Org.",
        ["publisher-a.example.org"],
    )


@pytest.mark.parametrize("domains", [
    ["publisher-a.example.org."],
    ["publisher-a.example.org", "publisher-a.example.org"],
    ["127.0.0.1"],
    ["publisher-a..example.org"],
    ["-publisher-a.example.org"],
])
def test_registered_domain_allowlist_rejects_noncanonical_or_unsafe_domains(domains):
    assert collection_module._valid_domain_allowlist(domains) is False


def test_registered_domain_allowlist_requires_canonical_ascii_domains():
    assert collection_module._valid_domain_allowlist(["publisher-a.example.org"]) is True
    assert collection_module._valid_domain_allowlist(["xn--bcher-kva.example"]) is True



@pytest.mark.parametrize("domains", [
    ("publisher-a.example.org.",),
    ("127.0.0.1",),
    ("publisher-a.example.org", "publisher-a.example.org"),
    ["publisher-a.example.org"],
])
def test_connector_execution_context_rejects_noncanonical_domain_scope(domains):
    with pytest.raises(ValueError, match="canonical DNS"):
        CollectionExecutionContext(domains)



def test_sqlite_storage_rejects_noncanonical_source_domain_allowlist():
    with populated_registry() as registry:
        connector = FixtureConnector([
            {"locator": "https://publisher-a.example.org/news/1"}
        ])
        sink = InMemoryObservationSink()
        result = make_collector(registry, connector, sink).collect(
            make_request(), "fixture-feed"
        )
        assert result.allowed is True

        row = registry._connection.execute(
            "SELECT payload_json FROM sources WHERE id = ?",
            ("source:publisher-a",),
        ).fetchone()
        source = json.loads(row["payload_json"])
        source["domains"] = ["publisher-a.example.org."]
        registry._connection.execute(
            "UPDATE sources SET payload_json = ? WHERE id = ?",
            (json.dumps(source), "source:publisher-a"),
        )

        with pytest.raises(RegistryIntegrityError, match="domain allowlist is invalid"):
            registry.append_many(result.observations)
        assert registry.collection_observations() == []



def test_registry_rejects_noncanonical_source_domains_at_creation():
    payload = source_payload()
    payload["domains"] = ["publisher-a.example.org."]
    with SQLiteRegistry() as registry:
        with pytest.raises(ContractValidationError, match="domains/0"):
            registry.add_source(payload)
        assert registry.get_source("source:publisher-a") is None


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf")])
def test_collection_request_rejects_non_finite_timeout(timeout_seconds):
    with pytest.raises(ValueError, match="timeout_seconds"):
        make_request(timeout_seconds=timeout_seconds)


@pytest.mark.parametrize("kwargs", [
    {"max_requests": True},
    {"max_requests": 1.5},
    {"window_seconds": float("nan")},
    {"window_seconds": float("inf")},
    {"window_seconds": float("-inf")},
    {"window_seconds": True},
])
def test_rate_limiter_rejects_non_finite_or_invalid_configuration(kwargs):
    with pytest.raises(ValueError, match="rate-limit settings"):
        InMemoryDomainRateLimiter(**kwargs)
