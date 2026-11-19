from __future__ import annotations

import sqlite3

import pytest

from sngso_engine.registry import (
    POLICY_VERSION,
    RegistryIntegrityError,
    RegistryMigrationError,
    SQLiteRegistry,
)


STAMP = "2026-11-19T00:00:00Z"


def source_record(source_id: str = "source:publisher-a") -> dict:
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


def endpoint_record(
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


def evidence_record(
    evidence_id: str = "permission-evidence:publisher-a-001",
    source_id: str = "source:publisher-a",
    review_status: str = "verified",
) -> dict:
    name = source_id.split(":", 1)[1]
    record = {
        "schema_version": "1.0.0",
        "id": evidence_id,
        "source_id": source_id,
        "evidence_type": "written_grant",
        "evidence_uri": f"https://{name}.example.org/permissions/letter",
        "captured_at": STAMP,
        "content_fingerprint": "sha256:" + "a" * 64,
        "scope_description": "Written permission for discovery of the named RSS endpoint.",
        "review_status": review_status,
    }
    if review_status != "unreviewed":
        record.update({"reviewed_at": STAMP, "reviewed_by": "rights-reviewer"})
    return record


def grant_record() -> dict:
    evidence_id = "permission-evidence:publisher-a-001"
    operations = {
        operation: {
            "allowed": operation == "discovery",
            "status": "approved" if operation == "discovery" else "denied",
            "conditions": [],
            **({"evidence_ids": [evidence_id]} if operation == "discovery" else {}),
        }
        for operation in (
            "discovery",
            "metadata",
            "excerpt_storage",
            "full_text_storage",
            "translation",
            "image_storage",
            "republication",
        )
    }
    return {
        "schema_version": "1.0.0",
        "id": "permission:publisher-a-discovery-001",
        "source_id": "source:publisher-a",
        "endpoint_ids": ["endpoint:publisher-a-rss"],
        "status": "approved",
        "operations": operations,
        "evidence_ids": [evidence_id],
        "basis": "Reviewed written permission for the named endpoint and operation.",
        "effective_from": "2026-11-18T00:00:00Z",
        "effective_until": "2026-12-18T00:00:00Z",
        "created_at": STAMP,
        "reviewed_at": STAMP,
        "reviewed_by": "rights-reviewer",
    }


def populate_registry(registry: SQLiteRegistry) -> None:
    registry.add_source(source_record())
    registry.add_endpoint(endpoint_record())
    registry.add_permission_evidence(evidence_record())


def test_migration_creates_versioned_schema_and_is_idempotent(tmp_path) -> None:
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database) as registry:
        assert registry.schema_version() == 1
        registry.add_source(source_record())
    with SQLiteRegistry(database) as reopened:
        assert reopened.schema_version() == 1
        assert reopened.get_source("source:publisher-a").publisher_name == "Publisher publisher-a"


def test_typed_registry_records_round_trip(tmp_path) -> None:
    with SQLiteRegistry(tmp_path / "registry.sqlite") as registry:
        source = registry.add_source(source_record())
        endpoint = registry.add_endpoint(endpoint_record())
        evidence = registry.add_permission_evidence(evidence_record())
        grant = registry.add_permission_grant(grant_record())

        assert source.to_payload() == source_record()
        assert registry.get_endpoint(endpoint.id).to_payload() == endpoint_record()
        assert registry.get_permission_evidence(evidence.id).to_payload() == evidence_record()
        assert registry.get_permission_grant(grant.id).to_payload() == grant_record()


def test_endpoint_cannot_reference_unknown_source() -> None:
    with SQLiteRegistry() as registry:
        with pytest.raises(RegistryIntegrityError, match="unknown source"):
            registry.add_endpoint(endpoint_record(source_id="source:missing"))


def test_permission_grant_rejects_endpoint_from_another_source() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_source(source_record("source:publisher-b"))
        registry.add_endpoint(endpoint_record("endpoint:publisher-b-rss", "source:publisher-b"))
        registry.add_permission_evidence(evidence_record())
        record = grant_record()
        record["endpoint_ids"] = ["endpoint:publisher-b-rss"]
        with pytest.raises(RegistryIntegrityError, match="another source"):
            registry.add_permission_grant(record)


def test_permission_grant_rejects_evidence_from_another_source() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_source(source_record("source:publisher-b"))
        registry.add_endpoint(endpoint_record())
        registry.add_permission_evidence(
            evidence_record("permission-evidence:publisher-b-001", "source:publisher-b")
        )
        record = grant_record()
        record["evidence_ids"] = ["permission-evidence:publisher-b-001"]
        record["operations"]["discovery"]["evidence_ids"] = [
            "permission-evidence:publisher-b-001"
        ]
        with pytest.raises(RegistryIntegrityError, match="another source"):
            registry.add_permission_grant(record)


def test_permission_grant_rejects_unverified_evidence_for_approval() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_endpoint(endpoint_record())
        registry.add_permission_evidence(evidence_record(review_status="unreviewed"))
        with pytest.raises(RegistryIntegrityError, match="verified evidence"):
            registry.add_permission_grant(grant_record())


def test_failed_grant_insert_rolls_back_without_partial_records() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_endpoint(endpoint_record())
        before = len(registry.audit_events())
        with pytest.raises(RegistryIntegrityError, match="evidence is missing"):
            registry.add_permission_grant(grant_record())
        assert registry.get_permission_grant("permission:publisher-a-discovery-001") is None
        assert len(registry.audit_events()) == before
        assert registry.verify_audit_chain()


def test_evaluator_uses_persisted_source_endpoint_and_verified_evidence() -> None:
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())

        allowed = registry.evaluate_grant(
            "permission:publisher-a-discovery-001",
            "discovery",
            endpoint_id="endpoint:publisher-a-rss",
        )
        wrong_endpoint = registry.evaluate_grant(
            "permission:publisher-a-discovery-001",
            "discovery",
            endpoint_id="endpoint:another-feed",
        )
        disallowed_operation = registry.evaluate_grant(
            "permission:publisher-a-discovery-001",
            "full_text_storage",
            endpoint_id="endpoint:publisher-a-rss",
        )

        assert (allowed.allowed, allowed.reason) == (True, "approved")
        assert (wrong_endpoint.allowed, wrong_endpoint.reason) == (
            False,
            "endpoint_scope_mismatch",
        )
        assert (disallowed_operation.allowed, disallowed_operation.reason) == (
            False,
            "operation_not_approved",
        )


def test_grant_revocation_is_versioned_and_immediately_denies() -> None:
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        revoked = registry.transition_permission_grant(
            "permission:publisher-a-discovery-001",
            "revoked",
            actor="rights-reviewer",
            reason="Publisher withdrew permission.",
        )
        decision = registry.evaluate_grant(
            revoked.id,
            "discovery",
            endpoint_id="endpoint:publisher-a-rss",
        )

        history = registry.grant_history(revoked.id)
        assert revoked.status == "revoked"
        assert [item["revision"] for item in history] == [1, 2]
        assert (decision.allowed, decision.reason) == (False, "permission_not_approved")


def test_invalid_grant_transition_is_rejected() -> None:
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        registry.transition_permission_grant(
            "permission:publisher-a-discovery-001",
            "revoked",
            actor="rights-reviewer",
            reason="Withdrawal",
        )
        with pytest.raises(RegistryIntegrityError, match="invalid permission grant transition"):
            registry.transition_permission_grant(
                "permission:publisher-a-discovery-001",
                "approved",
                actor="rights-reviewer",
                reason="Cannot reactivate revoked grant",
            )


def test_audit_chain_records_policy_version_and_is_append_only() -> None:
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        registry.evaluate_grant(
            "permission:publisher-a-discovery-001",
            "discovery",
            endpoint_id="endpoint:publisher-a-rss",
        )
        events = registry.audit_events()
        decision_events = [event for event in events if event["event_type"] == "permission.decision"]

        assert registry.verify_audit_chain()
        assert len(decision_events) == 1
        assert decision_events[0]["policy_version"] == POLICY_VERSION
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE audit_events SET event_type = 'tampered' WHERE sequence = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute("DELETE FROM audit_events WHERE sequence = 1")


def test_grant_revision_history_is_append_only() -> None:
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE permission_grant_revisions SET reason = 'tampered' WHERE revision = 1"
            )


def test_unknown_future_schema_version_is_rejected(tmp_path) -> None:
    database = tmp_path / "future.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 99")
    connection.close()
    with pytest.raises(RegistryMigrationError, match="newer than supported"):
        SQLiteRegistry(database)


def test_failed_migration_rolls_back_schema_changes(tmp_path) -> None:
    database = tmp_path / "broken.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sources (id TEXT PRIMARY KEY)")
    connection.close()

    with pytest.raises(RegistryMigrationError, match="rolled back"):
        SQLiteRegistry(database)

    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "sources" in tables
    assert "schema_migrations" not in tables
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    connection.close()


def test_invalid_operation_input_denies_without_crashing() -> None:
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        decision = registry.evaluate_grant(
            "permission:publisher-a-discovery-001",
            ["discovery"],
            endpoint_id="endpoint:publisher-a-rss",
        )
        assert (decision.allowed, decision.reason) == (False, "unknown_operation")
