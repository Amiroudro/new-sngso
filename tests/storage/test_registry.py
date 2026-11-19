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
        decision_payload = decision_events[0]["payload"]
        assert decision_payload["grant_revision"] == 1
        assert decision_payload["permission_record_hash"].startswith("sha256:")
        assert decision_payload["evidence_statuses"] == {
            "permission-evidence:publisher-a-001": "verified"
        }
        assert decision_payload["allowed"] is True
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


def article_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "article:publisher-a-001",
        "source_id": "source:publisher-a",
        "canonical_uri": "https://publisher-a.example.org/news/001",
        "title": "A sample report",
        "source_language": "en",
        "discovered_at": STAMP,
        "record_status": "active",
    }


def story_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "story:sample-event",
        "canonical_title": "A sample event",
        "status": "developing",
        "article_ids": ["article:publisher-a-001"],
        "created_at": STAMP,
        "updated_at": STAMP,
        "revision": 1,
    }


def claim_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "claim:sample-event-001",
        "story_id": "story:sample-event",
        "text": "The event began on the stated date.",
        "language": "en",
        "assessment": "unassessed",
        "provenance": [
            {
                "source_id": "source:publisher-a",
                "article_id": "article:publisher-a-001",
                "observed_at": STAMP,
                "method": "manual_fixture",
                "method_version": "1.0",
            }
        ],
        "created_at": STAMP,
    }


def factual_evidence_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "evidence:sample-event-001",
        "claim_id": "claim:sample-event-001",
        "source_id": "source:publisher-a",
        "article_id": "article:publisher-a-001",
        "relationship": "supports",
        "assessment_method": "human",
        "locator": {"uri": "https://publisher-a.example.org/news/001"},
        "assessed_at": STAMP,
        "assessed_by": "editor",
    }


def edition_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "edition:sample-2026-11-19",
        "edition_key": "2026-11-19",
        "created_at": STAMP,
        "status": "draft",
        "content_hash": "sha256:" + "b" * 64,
        "pipeline_run_id": "pipeline-run:sample-001",
        "story_ids": ["story:sample-event"],
        "language_variants": [{"language": "en", "status": "draft"}],
    }


def populate_editorial_records(registry: SQLiteRegistry) -> None:
    registry.add_source(source_record())
    registry.add_domain_record("article", article_record())
    registry.add_domain_record("story", story_record())
    registry.add_domain_record("claim", claim_record())
    registry.add_domain_record("evidence", factual_evidence_record())
    registry.add_domain_record("edition", edition_record())


def test_editorial_domain_records_round_trip_with_typed_models() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        assert registry.get_domain_record("article", "article:publisher-a-001").title == "A sample report"
        assert registry.get_domain_record("story", "story:sample-event").canonical_title == "A sample event"
        assert registry.get_domain_record("claim", "claim:sample-event-001").assessment == "unassessed"
        assert registry.get_domain_record("evidence", "evidence:sample-event-001").relationship == "supports"
        assert registry.get_domain_record("edition", "edition:sample-2026-11-19").status == "draft"


def test_story_cannot_reference_unknown_article() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        record = story_record()
        record["article_ids"] = ["article:not-yet-stored"]
        with pytest.raises(RegistryIntegrityError, match="unknown article"):
            registry.add_domain_record("story", record)


def test_claim_cannot_reference_unknown_story() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        record = claim_record()
        record["story_id"] = "story:missing"
        with pytest.raises(RegistryIntegrityError, match="unknown story"):
            registry.add_domain_record("claim", record)


def test_factual_evidence_cannot_cross_article_source_boundary() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_source(source_record("source:publisher-b"))
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        registry.add_domain_record("claim", claim_record())
        record = factual_evidence_record()
        record["source_id"] = "source:publisher-b"
        with pytest.raises(RegistryIntegrityError, match="article and source do not match"):
            registry.add_domain_record("evidence", record)


def test_domain_record_revision_is_audited_and_uses_optimistic_locking() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        revised = story_record()
        revised["status"] = "ongoing"
        revised["updated_at"] = "2026-11-20T00:00:00Z"
        record = registry.revise_domain_record(
            "story",
            "story:sample-event",
            revised,
            expected_revision=1,
            actor="editor",
            reason="Additional reporting confirms the story remains active.",
        )
        assert record.status == "ongoing"
        assert [item["revision"] for item in registry.domain_record_history("story", record.id)] == [1, 2]
        with pytest.raises(RegistryIntegrityError, match="revision conflict"):
            registry.revise_domain_record(
                "story",
                "story:sample-event",
                revised,
                expected_revision=1,
                actor="editor",
                reason="Stale write must not overwrite the newer revision.",
            )


def test_published_edition_snapshot_cannot_be_revised() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        edition = edition_record()
        edition["status"] = "published"
        registry.add_domain_record("edition", edition)
        with pytest.raises(RegistryIntegrityError, match="snapshots are immutable"):
            registry.revise_domain_record(
                "edition",
                edition["id"],
                edition,
                expected_revision=1,
                actor="editor",
                reason="Published snapshots must remain immutable.",
            )


def test_domain_record_revisions_are_append_only() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM domain_record_revisions WHERE record_type = 'story'"
            )


def test_pending_grant_requires_verified_evidence_before_approval() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_endpoint(endpoint_record())
        registry.add_permission_evidence(evidence_record(review_status="unreviewed"))
        pending = grant_record()
        pending["status"] = "pending"
        pending["operations"]["discovery"] = {
            "allowed": False,
            "status": "denied",
            "conditions": [],
        }
        pending.pop("reviewed_at")
        pending.pop("reviewed_by")
        registry.add_permission_grant(pending)
        with pytest.raises(RegistryIntegrityError, match="verified evidence"):
            registry.transition_permission_grant(
                pending["id"],
                "approved",
                actor="rights-reviewer",
                reason="Evidence is not verified yet.",
            )
        assert registry.get_permission_grant(pending["id"]).status == "pending"
        assert [item["revision"] for item in registry.grant_history(pending["id"])] == [1]


def test_allowed_operation_evidence_must_be_in_grant_evidence_set() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_endpoint(endpoint_record())
        registry.add_permission_evidence(evidence_record())
        registry.add_permission_evidence(
            evidence_record("permission-evidence:publisher-a-002")
        )
        record = grant_record()
        record["operations"]["discovery"]["evidence_ids"] = [
            "permission-evidence:publisher-a-002"
        ]
        with pytest.raises(RegistryIntegrityError, match="also appear in grant evidence_ids"):
            registry.add_permission_grant(record)


def test_audit_hash_chain_detects_database_level_tampering() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        assert registry.verify_audit_chain()
        registry._connection.execute("DROP TRIGGER audit_events_no_update")
        registry._connection.execute(
            "UPDATE audit_events SET event_type = 'tampered' WHERE sequence = 1"
        )
        assert registry.verify_audit_chain() is False


def test_two_registry_instances_serialize_concurrent_writes(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    database = tmp_path / "shared.sqlite"
    first = SQLiteRegistry(database)
    second = SQLiteRegistry(database)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda pair: pair[0].add_source(source_record(pair[1])).id,
                    [(first, "source:parallel-a"), (second, "source:parallel-b")],
                )
            )
        assert set(results) == {"source:parallel-a", "source:parallel-b"}
        assert first.verify_audit_chain()
    finally:
        first.close()
        second.close()
