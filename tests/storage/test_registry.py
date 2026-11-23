from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from sngso_engine import registry as registry_module
from sngso_engine.contracts import ContractValidationError

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
        "scope_description": "Written permission for discovery and benchmark excerpt storage at the named RSS endpoint.",
        "review_status": review_status,
    }
    if review_status != "unreviewed":
        record.update({"reviewed_at": STAMP, "reviewed_by": "rights-reviewer"})
    return record


def grant_record() -> dict:
    evidence_id = "permission-evidence:publisher-a-001"
    operations = {
        operation: {
            "allowed": operation in {"discovery", "excerpt_storage"},
            "status": "approved" if operation in {"discovery", "excerpt_storage"} else "denied",
            "conditions": [],
            **({"evidence_ids": [evidence_id]} if operation in {"discovery", "excerpt_storage"} else {}),
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
        assert registry.schema_version() == 35
        registry.add_source(source_record())
    with SQLiteRegistry(database) as reopened:
        assert reopened.schema_version() == 35
        assert reopened.get_source("source:publisher-a").publisher_name == "Publisher publisher-a"


def test_existing_schema_v2_database_migrates_to_v7(tmp_path, monkeypatch):
    database = tmp_path / "registry-v2.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 2)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 2
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 7)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 7
        columns = {
            row["name"]
            for row in migrated._connection.execute(
                "PRAGMA table_info(collection_observations)"
            ).fetchall()
        }
        assert "http_method" in columns
        tables = {
            row["name"]
            for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "ingestion_decisions" in tables


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


def test_edition_snapshot_cannot_be_revised() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        edition = edition_record()
        edition["status"] = "approved"
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
        for operation in ("discovery", "excerpt_storage"):
            pending["operations"][operation] = {
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


def test_audit_checkpoint_exposes_chain_tip_for_external_anchoring() -> None:
    with SQLiteRegistry() as registry:
        assert registry.audit_checkpoint() == {"sequence": 0, "event_hash": None}
        registry.add_source(source_record())
        checkpoint = registry.audit_checkpoint()
        assert checkpoint["sequence"] == len(registry.audit_events())
        assert checkpoint["event_hash"] == registry.audit_events()[-1]["event_hash"]
        assert registry.verify_audit_chain()


def test_audit_chain_verifier_alone_cannot_detect_tail_truncation() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_endpoint(endpoint_record())
        external_checkpoint = registry.audit_checkpoint()

        # Simulate a privileged file-level attacker disabling local protections.
        registry._connection.execute("DROP TRIGGER audit_events_no_delete")
        registry._connection.execute(
            "DELETE FROM audit_events WHERE sequence = ?", (external_checkpoint["sequence"],)
        )

        # The remaining prefix is internally valid, so an external checkpoint
        # is required to notice that the previously anchored tip disappeared.
        assert registry.verify_audit_chain() is True
        assert registry.audit_checkpoint() != external_checkpoint


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

def test_failed_commit_rolls_back_and_leaves_connection_usable() -> None:
    with SQLiteRegistry() as registry:
        with pytest.raises(sqlite3.IntegrityError):
            with registry._transaction() as connection:
                connection.execute("PRAGMA defer_foreign_keys = ON")
                connection.execute(
                    """INSERT INTO source_endpoints
                    (id, source_id, endpoint_uri, status, created_at, updated_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "endpoint:orphan",
                        "source:missing",
                        "https://missing.example.org/feed.xml",
                        "active",
                        STAMP,
                        STAMP,
                        "{}",
                    ),
                )

        assert registry._connection.in_transaction is False
        assert registry.get_endpoint("endpoint:orphan") is None

        # The same connection remains usable after the failed COMMIT.
        registry.add_source(source_record())
        assert registry.get_source("source:publisher-a") is not None


def test_assessed_claim_must_link_to_its_own_evidence():
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        claim = claim_record()
        claim["assessment"] = "supported"
        claim["evidence_ids"] = ["evidence:sample-event-001"]
        claim["reviewed_at"] = STAMP
        claim["review_note"] = "The source directly supports this proposition."
        revised = registry.revise_domain_record(
            "claim", claim["id"], claim, expected_revision=1,
            actor="editor", reason="Reviewed the supporting source passage.",
        )
        assert revised.assessment == "supported"


def test_claim_cannot_link_evidence_owned_by_another_claim():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        registry.add_domain_record("claim", claim_record())
        second_claim = claim_record()
        second_claim["id"] = "claim:sample-event-002"
        registry.add_domain_record("claim", second_claim)
        registry.add_domain_record("evidence", factual_evidence_record())
        second_claim["evidence_ids"] = ["evidence:sample-event-001"]
        with pytest.raises(RegistryIntegrityError, match="belongs to another claim"):
            registry.revise_domain_record(
                "claim", second_claim["id"], second_claim, expected_revision=1,
                actor="editor", reason="Attempted cross-claim evidence link.",
            )


def test_supported_claim_cannot_ignore_contradicting_evidence():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        registry.add_domain_record("claim", claim_record())
        registry.add_domain_record("evidence", factual_evidence_record())
        contradiction = factual_evidence_record()
        contradiction["id"] = "evidence:sample-event-contradiction"
        contradiction["relationship"] = "contradicts"
        registry.add_domain_record("evidence", contradiction)
        claim = claim_record()
        claim["evidence_ids"] = [
            "evidence:sample-event-001",
            "evidence:sample-event-contradiction",
        ]
        claim["assessment"] = "supported"
        claim["reviewed_at"] = STAMP
        claim["review_note"] = "Initial assessment pending contradiction review."
        with pytest.raises(RegistryIntegrityError, match="no unresolved contradiction"):
            registry.revise_domain_record(
                "claim", claim["id"], claim, expected_revision=1,
                actor="editor", reason="Incorrectly marked as fully supported.",
            )
        claim["assessment"] = "disputed"
        revised = registry.revise_domain_record(
            "claim", claim["id"], claim, expected_revision=1,
            actor="editor", reason="Sources materially disagree.",
        )
        assert revised.assessment == "disputed"


def test_retracted_claim_requires_a_review_note():
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        claim = claim_record()
        claim["assessment"] = "retracted"
        claim["evidence_ids"] = ["evidence:sample-event-001"]
        claim["reviewed_at"] = STAMP
        with pytest.raises(RegistryIntegrityError, match="review_note"):
            registry.revise_domain_record(
                "claim", claim["id"], claim, expected_revision=1,
                actor="editor", reason="Retraction needs an explanation.",
            )
        claim["review_note"] = "The original proposition was withdrawn after correction."
        revised = registry.revise_domain_record(
            "claim", claim["id"], claim, expected_revision=1,
            actor="editor", reason="The proposition was formally retracted.",
        )
        assert revised.assessment == "retracted"


def test_evidence_locator_must_stay_inside_registered_source_domains():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        registry.add_domain_record("claim", claim_record())
        evidence = factual_evidence_record()
        evidence["locator"]["uri"] = "https://unregistered.example.net/report"
        with pytest.raises(RegistryIntegrityError, match="outside its registered source domains"):
            registry.add_domain_record("evidence", evidence)


def _two_story_decision_registry(registry: SQLiteRegistry, operation: str) -> str:
    registry.add_source(source_record())
    first_article = article_record()
    second_article = article_record()
    second_article.update({
        "id": "article:publisher-a-002",
        "canonical_uri": "https://publisher-a.example.org/news/002",
        "title": "A second sample report",
    })
    registry.add_domain_record("article", first_article)
    registry.add_domain_record("article", second_article)

    first_story = story_record()
    second_story = story_record()
    second_story.update({
        "id": "story:sample-event-002",
        "canonical_title": "A related sample event",
        "article_ids": ["article:publisher-a-002"],
    })
    registry.add_domain_record("story", first_story)
    registry.add_domain_record("story", second_story)

    first_claim = claim_record()
    second_claim = claim_record()
    second_claim.update({
        "id": "claim:sample-event-002",
        "story_id": "story:sample-event-002",
        "text": "A second attributable proposition.",
        "provenance": [{
            "source_id": "source:publisher-a",
            "article_id": "article:publisher-a-002",
            "observed_at": STAMP,
            "method": "manual_fixture",
            "method_version": "1.0",
        }],
    })
    registry.add_domain_record("claim", first_claim)
    registry.add_domain_record("claim", second_claim)
    registry.add_domain_record("evidence", factual_evidence_record())

    edition = edition_record()
    edition["story_ids"] = ["story:sample-event", "story:sample-event-002"]
    registry.add_domain_record("edition", edition)

    if operation == "merge":
        article_assignments = [
            {"article_id": "article:publisher-a-001", "assigned_story_id": "story:sample-event"},
            {"article_id": "article:publisher-a-002", "assigned_story_id": "story:sample-event"},
        ]
        claim_assignments = [
            {"claim_id": "claim:sample-event-001", "assigned_story_id": "story:sample-event", "expected_revision": 1},
            {"claim_id": "claim:sample-event-002", "assigned_story_id": "story:sample-event", "expected_revision": 1},
        ]
    else:
        article_assignments = [
            {"article_id": "article:publisher-a-001", "assigned_story_id": "story:sample-event"},
            {"article_id": "article:publisher-a-002", "assigned_story_id": "story:sample-event-002"},
        ]
        claim_assignments = [
            {"claim_id": "claim:sample-event-001", "assigned_story_id": "story:sample-event", "expected_revision": 1},
            {"claim_id": "claim:sample-event-002", "assigned_story_id": "story:sample-event-002", "expected_revision": 1},
        ]
    decision = {
        "schema_version": "1.0.0",
        "id": f"story-decision:sample-{operation}",
        "operation": operation,
        "story_ids": ["story:sample-event", "story:sample-event-002"],
        "canonical_story_id": "story:sample-event",
        "expected_story_revisions": [
            {"story_id": "story:sample-event", "expected_revision": 1},
            {"story_id": "story:sample-event-002", "expected_revision": 1},
        ],
        "article_assignments": article_assignments,
        "claim_assignments": claim_assignments,
        "decided_by": "editor",
        "rationale": f"Fixture {operation} decision with explicit allocations.",
        "decided_at": STAMP,
    }
    registry.add_domain_record("story_decision", decision)
    return decision["id"]


def test_atomic_story_merge_moves_membership_and_claims_preserving_history() -> None:
    with SQLiteRegistry() as registry:
        decision_id = _two_story_decision_registry(registry, "merge")
        before_edition = registry.get_domain_record("edition", "edition:sample-2026-11-19").to_payload()
        preview = registry.preview_story_decision_application(decision_id)
        assert preview["applicable"] is True
        applied = registry.apply_story_decision(
            decision_id,
            expected_impact_fingerprint=preview["impact_fingerprint"],
            actor="senior-editor",
            rationale="The two reports describe the same verified event.",
        )
        assert applied["already_applied"] is False
        assert registry.get_domain_record("story", "story:sample-event").to_payload()["article_ids"] == [
            "article:publisher-a-001", "article:publisher-a-002"
        ]
        archived = registry.get_domain_record("story", "story:sample-event-002").to_payload()
        assert archived["status"] == "archived"
        assert archived["article_ids"] == []
        assert registry.get_domain_record("claim", "claim:sample-event-002").to_payload()["story_id"] == "story:sample-event"
        assert applied["result"]["preserved_evidence_ids"] == ["evidence:sample-event-001"]
        assert registry.get_domain_record("edition", "edition:sample-2026-11-19").to_payload() == before_edition
        assert len(registry.domain_record_history("story", "story:sample-event")) == 2
        assert len(registry.domain_record_history("claim", "claim:sample-event-002")) == 2
        assert registry.verify_audit_chain()


def test_atomic_story_split_keeps_each_existing_story_and_claim_allocation() -> None:
    with SQLiteRegistry() as registry:
        decision_id = _two_story_decision_registry(registry, "split")
        preview = registry.preview_story_decision_application(decision_id)
        assert preview["applicable"] is True
        applied = registry.apply_story_decision(
            decision_id,
            expected_impact_fingerprint=preview["impact_fingerprint"],
            actor="editor",
            rationale="Separate the two independently attributable developments.",
        )
        assert applied["result"]["operation"] == "split"
        assert registry.get_domain_record("story", "story:sample-event").to_payload()["article_ids"] == [
            "article:publisher-a-001"
        ]
        assert registry.get_domain_record("story", "story:sample-event-002").to_payload()["article_ids"] == [
            "article:publisher-a-002"
        ]
        assert registry.get_domain_record("claim", "claim:sample-event-002").to_payload()["story_id"] == "story:sample-event-002"
        assert registry.verify_audit_chain()


def test_story_decision_application_is_idempotent_on_retry() -> None:
    with SQLiteRegistry() as registry:
        decision_id = _two_story_decision_registry(registry, "merge")
        preview = registry.preview_story_decision_application(decision_id)
        first = registry.apply_story_decision(
            decision_id,
            expected_impact_fingerprint=preview["impact_fingerprint"],
            actor="editor",
            rationale="Merge after review.",
        )
        audit_count = len(registry.audit_events())
        retry = registry.apply_story_decision(
            decision_id,
            expected_impact_fingerprint=preview["impact_fingerprint"],
            actor="another-editor",
            rationale="Retry after a lost response.",
        )
        assert retry["already_applied"] is True
        assert retry["application_id"] == first["application_id"]
        assert retry["result"] == first["result"]
        assert len(registry.audit_events()) == audit_count
        assert registry.preview_story_decision_application(decision_id)["already_applied"] is True


def test_story_decision_application_rejects_stale_preview_fingerprint() -> None:
    with SQLiteRegistry() as registry:
        decision_id = _two_story_decision_registry(registry, "merge")
        preview = registry.preview_story_decision_application(decision_id)
        with pytest.raises(RegistryIntegrityError, match="preview is stale"):
            registry.apply_story_decision(
                decision_id,
                expected_impact_fingerprint="sha256:" + "0" * 64,
                actor="editor",
                rationale="Attempt with an obsolete preview.",
            )
        assert registry.get_domain_record("story", "story:sample-event").to_payload()["article_ids"] == [
            "article:publisher-a-001"
        ]
        assert registry.verify_audit_chain()


def test_story_decision_application_rolls_back_every_write_on_audit_failure(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        decision_id = _two_story_decision_registry(registry, "merge")
        preview = registry.preview_story_decision_application(decision_id)
        checkpoint = registry.audit_checkpoint()
        story_history_before = registry.domain_record_history("story", "story:sample-event")
        claim_history_before = registry.domain_record_history("claim", "claim:sample-event-002")

        def fail_audit(*args, **kwargs):
            raise RuntimeError("injected audit failure")

        monkeypatch.setattr(registry, "_append_audit", fail_audit)
        with pytest.raises(RuntimeError, match="injected audit failure"):
            registry.apply_story_decision(
                decision_id,
                expected_impact_fingerprint=preview["impact_fingerprint"],
                actor="editor",
                rationale="This must roll back.",
            )

        assert registry.get_domain_record("story", "story:sample-event").to_payload()["article_ids"] == [
            "article:publisher-a-001"
        ]
        assert registry.get_domain_record("claim", "claim:sample-event-002").to_payload()["story_id"] == "story:sample-event-002"
        assert registry.domain_record_history("story", "story:sample-event") == story_history_before
        assert registry.domain_record_history("claim", "claim:sample-event-002") == claim_history_before
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM story_decision_applications WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0] == 0
        assert registry.audit_checkpoint() == checkpoint
        assert registry.verify_audit_chain()


def test_split_decision_cannot_drop_an_unassigned_article() -> None:
    with SQLiteRegistry() as registry:
        decision_id = _two_story_decision_registry(registry, "split")
        third_article = article_record()
        third_article.update({
            "id": "article:publisher-a-003",
            "canonical_uri": "https://publisher-a.example.org/news/003",
            "title": "A third sample report",
        })
        registry.add_domain_record("article", third_article)
        story = registry.get_domain_record("story", "story:sample-event").to_payload()
        story["article_ids"].append("article:publisher-a-003")
        registry.revise_domain_record(
            "story", story["id"], story, expected_revision=1,
            actor="editor", reason="Add a third article before testing split completeness.",
        )
        row = registry._connection.execute(
            "SELECT payload_json FROM story_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        incomplete = __import__("json").loads(row["payload_json"])
        incomplete["id"] = "story-decision:sample-split-incomplete"
        incomplete["expected_story_revisions"][0]["expected_revision"] = 2
        with pytest.raises(RegistryIntegrityError, match="must account for every article"):
            registry.add_domain_record("story_decision", incomplete)


def test_concurrent_story_decision_apply_creates_one_application(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    database = tmp_path / "concurrent-story-apply.sqlite"
    first = SQLiteRegistry(database)
    second = None
    try:
        decision_id = _two_story_decision_registry(first, "merge")
        preview = first.preview_story_decision_application(decision_id)
        second = SQLiteRegistry(database)

        def apply(registry: SQLiteRegistry) -> dict:
            return registry.apply_story_decision(
                decision_id,
                expected_impact_fingerprint=preview["impact_fingerprint"],
                actor="editor",
                rationale="Concurrent retry must not duplicate the application.",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(apply, [first, second]))

        assert results[0]["application_id"] == results[1]["application_id"]
        assert sorted(result["already_applied"] for result in results) == [False, True]
        assert first._connection.execute(
            "SELECT COUNT(*) FROM story_decision_applications WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0] == 1
        assert len(first.domain_record_history("story", "story:sample-event")) == 2
        assert len(first.domain_record_history("claim", "claim:sample-event-002")) == 2
        assert first.verify_audit_chain()
    finally:
        if second is not None:
            second.close()
        first.close()


def _timeline_event_record(event_id: str = "timeline-event:sample-001") -> dict:
    return {
        "schema_version": "1.0.0",
        "id": event_id,
        "story_id": "story:sample-event",
        "title": "The event was publicly announced",
        "event_type": "announcement",
        "occurred": {"precision": "date", "start": "2026-11-18"},
        "claim_ids": ["claim:sample-event-001"],
        "created_at": STAMP,
        "created_by": "editor",
        "rationale": "The linked claim records the announcement date from the cited report.",
    }


def test_timeline_event_round_trip_and_chronological_order() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        later = _timeline_event_record("timeline-event:later")
        later["occurred"] = {"precision": "date", "start": "2026-11-19"}
        earlier = _timeline_event_record("timeline-event:earlier")
        earlier["occurred"] = {"precision": "date", "start": "2026-11-17"}
        unknown = _timeline_event_record("timeline-event:unknown")
        unknown["occurred"] = {"precision": "unknown"}
        registry.add_timeline_event(later)
        registry.add_timeline_event(unknown)
        registry.add_timeline_event(earlier)
        assert registry.get_timeline_event(later["id"]).to_payload() == later
        assert [item["event_id"] for item in registry.story_timeline("story:sample-event")] == [
            "timeline-event:earlier", "timeline-event:later", "timeline-event:unknown"
        ]
        assert registry.verify_audit_chain()


def test_timeline_event_preserves_approximate_date_and_uncertainty_note() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        event = _timeline_event_record()
        event["occurred"] = {
            "precision": "approximate",
            "start": "2026-11-18",
            "note": "The source says the event happened around this date.",
        }
        saved = registry.add_timeline_event(event)
        assert saved.to_payload()["occurred"] == event["occurred"]


@pytest.mark.parametrize(
    "occurred",
    [
        {"precision": "instant", "start": "2026-11-18T12:00:00"},
        {"precision": "date", "start": "2026-02-30"},
        {"precision": "range", "start": "2026-11-19T00:00:00Z", "end": "2026-11-18T00:00:00Z"},
        {"precision": "approximate", "start": "2026-11-18"},
        {"precision": "unknown", "start": "2026-11-18"},
    ],
)
def test_timeline_event_rejects_invalid_temporal_precision(occurred) -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        event = _timeline_event_record()
        event["occurred"] = occurred
        with pytest.raises((RegistryIntegrityError, ContractValidationError)):
            registry.add_timeline_event(event)


def test_timeline_event_requires_claim_from_same_story() -> None:
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        event = _timeline_event_record()
        event["claim_ids"] = ["claim:missing"]
        with pytest.raises(RegistryIntegrityError, match="unknown claim"):
            registry.add_timeline_event(event)


def test_timeline_event_correction_appends_and_supersedes_without_rewriting() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        original = _timeline_event_record()
        registry.add_timeline_event(original)
        correction = _timeline_event_record("timeline-event:sample-correction")
        correction["title"] = "The announcement date was corrected"
        correction["occurred"] = {"precision": "date", "start": "2026-11-17"}
        correction["supersedes_event_id"] = original["id"]
        correction["rationale"] = "A later reviewed report corrects the earlier date."
        registry.add_timeline_event(correction)
        assert registry.get_timeline_event(original["id"]).to_payload() == original
        assert [item["event_id"] for item in registry.story_timeline("story:sample-event")] == [
            correction["id"]
        ]
        history = registry.timeline_event_history("story:sample-event")
        assert len(history) == 2
        assert next(item for item in history if item["event_id"] == original["id"])["is_superseded"]
        assert registry.verify_audit_chain()


def test_timeline_event_cannot_correct_an_event_from_another_story() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        original = _timeline_event_record()
        registry.add_timeline_event(original)
        correction = _timeline_event_record("timeline-event:bad-correction")
        correction["supersedes_event_id"] = "timeline-event:not-in-story"
        with pytest.raises(RegistryIntegrityError, match="same story"):
            registry.add_timeline_event(correction)


def test_timeline_events_are_append_only_at_sqlite_boundary() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        event = registry.add_timeline_event(_timeline_event_record())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE timeline_events SET payload_json = '{}' WHERE id = ?",
                (event.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM timeline_events WHERE id = ?", (event.id,)
            )


def test_existing_schema_v6_database_migrates_to_v7(tmp_path, monkeypatch) -> None:
    database = tmp_path / "registry-v6.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 6)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 6
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'timeline_events'"
        ).fetchone() is None
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 7)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 7
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'timeline_events'"
        ).fetchone() is not None


def test_timeline_event_rejects_invalid_iana_timezone() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        event = _timeline_event_record()
        event["occurred"] = {
            "precision": "instant",
            "start": "2026-11-18T12:00:00Z",
            "timezone": "Not/A_Real_Zone",
        }
        with pytest.raises(RegistryIntegrityError, match="valid IANA timezone"):
            registry.add_timeline_event(event)


def test_timeline_event_cannot_have_two_direct_corrections() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        original = _timeline_event_record()
        registry.add_timeline_event(original)
        for suffix in ("first", "second"):
            correction = _timeline_event_record(f"timeline-event:{suffix}")
            correction["supersedes_event_id"] = original["id"]
            correction["rationale"] = f"Correction {suffix}."
            if suffix == "first":
                registry.add_timeline_event(correction)
            else:
                with pytest.raises(sqlite3.IntegrityError):
                    registry.add_timeline_event(correction)


def test_timeline_event_sqlite_trigger_rejects_claim_from_another_story() -> None:
    with SQLiteRegistry() as registry:
        populate_editorial_records(registry)
        other_article = article_record()
        other_article.update({
            "id": "article:publisher-a-002",
            "canonical_uri": "https://publisher-a.example.org/news/002",
        })
        other_story = story_record()
        other_story.update({
            "id": "story:other-event",
            "article_ids": ["article:publisher-a-002"],
        })
        other_claim = claim_record()
        other_claim.update({
            "id": "claim:other-event",
            "story_id": "story:other-event",
            "provenance": [{
                "source_id": "source:publisher-a",
                "article_id": "article:publisher-a-002",
                "observed_at": STAMP,
                "method": "manual_fixture",
                "method_version": "1.0",
            }],
        })
        registry.add_domain_record("article", other_article)
        registry.add_domain_record("story", other_story)
        registry.add_domain_record("claim", other_claim)
        payload = _timeline_event_record()
        with pytest.raises(sqlite3.IntegrityError, match="claims must belong"):
            registry._connection.execute(
                """INSERT INTO timeline_events
                (id, story_id, occurred_sort_key, precision, supersedes_event_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "timeline-event:sql-invalid",
                    "story:sample-event",
                    "2026-11-18",
                    "date",
                    None,
                    STAMP,
                    __import__("json").dumps({**payload, "claim_ids": ["claim:other-event"]}),
                ),
            )


def test_existing_schema_v7_database_migrates_to_v8(tmp_path, monkeypatch) -> None:
    database = tmp_path / "registry-v7.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 7)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 7
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'perspectives'"
        ).fetchone() is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 8)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 8
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'perspectives'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 8


def test_existing_schema_v8_database_migrates_to_v9(tmp_path, monkeypatch) -> None:
    database = tmp_path / "registry-v8.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 8)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 8
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'claim_relations'"
        ).fetchone() is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 9)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 9
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'claim_relations'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 9


def test_existing_schema_v9_database_migrates_to_v10(tmp_path, monkeypatch) -> None:
    database = tmp_path / "registry-v9.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 9)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 9
        trigger = old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'timeline_events_review_validate_insert'"
        ).fetchone()
        assert trigger is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 10)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 10
        trigger = migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'timeline_events_review_validate_insert'"
        ).fetchone()
        assert trigger is not None
        migrated.migrate()
        assert migrated.schema_version() == 10


def test_existing_schema_v10_database_migrates_to_v11(tmp_path, monkeypatch) -> None:
    database = tmp_path / "registry-v10.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 10)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 10
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'learning_objects'"
        ).fetchone() is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 11)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 11
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'learning_objects'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 11


def test_existing_schema_v11_database_migrates_to_v12(tmp_path, monkeypatch) -> None:
    database = tmp_path / "registry-v11.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 11)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 11
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'glossary_entries'"
        ).fetchone() is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 12)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 12
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'glossary_entries'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 12


def _benchmark_review_payload() -> dict:
    return {
        "schema_version": "1.0.0",
        "review_id": "review:benchmark-en-001",
        "benchmark_id": "benchmark:headline-en-v1",
        "benchmark_fingerprint": registry_module.sha256_fingerprint({"schema_version": "1.0.0", "items": [{"item_id": "headline-en-001", "task_type": "headline", "input": "A report about a verified event", "expected_output": "Verified event report", "scoring_criteria": ["Preserves the central fact", "Does not add unsupported detail"]}]}),
        "language": "en",
        "status": "approved",
        "reviewer": {
            "reviewer_id": "editor:lead",
            "role": "English editor",
            "language": "en",
            "qualification_basis": "Professional English copy-editing and newsroom experience.",
        },
        "reviewed_at": "2026-11-20T00:00:00Z",
        "source_materials": [{
            "article_id": "article:publisher-a-001",
            "source_id": "source:publisher-a",
            "canonical_uri": "https://publisher-a.example.org/news/001",
            "content_fingerprint": "sha256:" + "d" * 64,
            "checked_at": "2026-11-19T18:00:00Z",
            "approved_use_reference": "permission-evidence:publisher-a-001",
            "permission_grant_id": "permission:publisher-a-discovery-001",
            "endpoint_id": "endpoint:publisher-a-rss",
            "permission_operation": "excerpt_storage",
        }],
        "corrections": [],
        "adjudication_note": "Checked against the registered article and source evidence.",
    }



def _register_benchmark_test_principals(registry: SQLiteRegistry, *, reviewer_kind: str = "unverified") -> None:
    registry.register_benchmark_principal(
        "benchmark-curator", display_name="Test curator",
        roles=["benchmark_curator"], provisioned_by="test-bootstrap",
        reason="Provision test curator",
    )
    registry.register_benchmark_principal(
        "curator", display_name="Curator",
        roles=["benchmark_curator"], provisioned_by="test-bootstrap",
        reason="Provision test curator",
    )
    for principal in ("editor:lead", "editor:second", "editor:en-01"):
        registry.register_benchmark_principal(
            principal, display_name=principal,
            roles=["benchmark_reviewer"], provisioned_by="test-bootstrap",
            reason="Provision test reviewer", languages=["en"], principal_kind=reviewer_kind,
        )
    registry.register_benchmark_principal(
        "adjudicator:senior", display_name="Senior adjudicator",
        roles=["benchmark_adjudicator"], provisioned_by="test-bootstrap",
        reason="Provision test adjudicator",
    )


def _populate_benchmark_review_sources(registry: SQLiteRegistry, *, reviewer_kind: str = "unverified") -> None:
    _register_benchmark_test_principals(registry, reviewer_kind=reviewer_kind)
    registry.add_source(source_record())
    registry.add_endpoint(endpoint_record())
    registry.add_permission_evidence(evidence_record())
    registry.add_permission_grant(grant_record())
    registry.add_domain_record("article", article_record())
    registry.register_benchmark_artifact(
        "benchmark:headline-en-v1",
        language="en",
        content={"schema_version": "1.0.0", "items": [{"item_id": "headline-en-001", "task_type": "headline", "input": "A report about a verified event", "expected_output": "Verified event report", "scoring_criteria": ["Preserves the central fact", "Does not add unsupported detail"]}]},
        actor="benchmark-curator",
        reason="register immutable test artifact",
    )


def _benchmark_assignment_payload(**overrides):
    payload = {
        "schema_version": "1.0.0",
        "assignment_id": "assignment:headline-en-001",
        "artifact_id": "benchmark:headline-en-v1",
        "artifact_fingerprint": registry_module.sha256_fingerprint({
            "schema_version": "1.0.0",
            "items": [{
                "item_id": "headline-en-001",
                "task_type": "headline",
                "input": "A report about a verified event",
                "expected_output": "Verified event report",
                "scoring_criteria": ["Preserves the central fact", "Does not add unsupported detail"],
            }],
        }),
        "language": "en",
        "reviewer_id": "editor:lead",
        "assigned_by": "curator",
        "assigned_at": "2026-11-20T00:00:00Z",
        "due_at": "2026-11-21T00:00:00Z",
        "status": "assigned",
        "reason": "Independent English benchmark review",
    }
    payload.update(overrides)
    return payload


def test_assignment_persists_with_immutable_revision_history_and_audit():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        saved = registry.create_benchmark_review_assignment(
            payload, actor="curator", reason="Assign independent English review",
        )
        assert saved["status"] == "assigned"
        assert saved["revision"] == 1
        history = registry.benchmark_review_assignment_history(payload["assignment_id"])
        assert len(history) == 1
        assert history[0]["actor"] == "curator"
        assert history[0]["assignment"]["artifact_fingerprint"] == payload["artifact_fingerprint"]
        assert registry.audit_events(entity_id=payload["assignment_id"])[0]["event_type"] == "benchmark_review_assignment.created"
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            registry._connection.execute(
                "DELETE FROM benchmark_review_assignments WHERE assignment_id = ?",
                (payload["assignment_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="matching append-only revision"):
            registry._connection.execute(
                "UPDATE benchmark_review_assignments SET revision = 2 WHERE assignment_id = ?",
                (payload["assignment_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE benchmark_review_assignment_revisions SET reason = 'tampered' WHERE assignment_id = ?",
                (payload["assignment_id"],),
            )


def test_assignment_requires_exact_artifact_and_current_language_qualified_reviewer():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload(artifact_fingerprint="sha256:" + "f" * 64)
        with pytest.raises(RegistryIntegrityError, match="exact artifact"):
            registry.create_benchmark_review_assignment(payload, actor="curator", reason="wrong fingerprint")
        registry.register_benchmark_principal(
            "editor:bangla-only", display_name="Bangla reviewer",
            roles=["benchmark_reviewer"], languages=["bn"],
            provisioned_by="test-bootstrap", reason="Bangla qualification",
        )
        payload = _benchmark_assignment_payload(
            assignment_id="assignment:wrong-language-reviewer",
            reviewer_id="editor:bangla-only",
        )
        with pytest.raises(RegistryIntegrityError, match="not qualified"):
            registry.create_benchmark_review_assignment(payload, actor="curator", reason="wrong language")


def test_assignment_requires_curator_and_prevents_duplicate_active_assignment():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload(assigned_by="editor:second")
        with pytest.raises(RegistryIntegrityError, match="lacks required role"):
            registry.create_benchmark_review_assignment(payload, actor="editor:second", reason="not curator")
        payload["assigned_by"] = "curator"
        registry.create_benchmark_review_assignment(payload, actor="curator", reason="first assignment")
        duplicate = _benchmark_assignment_payload(assignment_id="assignment:duplicate")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            registry.create_benchmark_review_assignment(duplicate, actor="curator", reason="duplicate active assignment")


def test_assignment_queue_filters_by_reviewer_status_and_language():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(payload, actor="curator", reason="assign")
        assert len(registry.list_benchmark_review_assignments(reviewer_id="editor:lead")) == 1
        assert len(registry.list_benchmark_review_assignments(status="assigned", language="en")) == 1
        assert registry.list_benchmark_review_assignments(reviewer_id="editor:second") == []
        with pytest.raises(ValueError, match="unsupported assignment status"):
            registry.list_benchmark_review_assignments(status="approved")


def test_assignment_acceptance_and_cancellation_are_persisted_as_revisions():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(payload, actor="curator", reason="assign")
        accepted = registry.transition_benchmark_review_assignment(
            payload["assignment_id"], event="accept", actor="editor:lead",
            at="2026-11-20T01:00:00Z", reason="Reviewer accepted",
        )
        assert accepted["status"] == "accepted"
        cancelled = registry.transition_benchmark_review_assignment(
            payload["assignment_id"], event="cancel", actor="curator",
            at="2026-11-20T02:00:00Z", reason="Reassigned by curator",
        )
        assert cancelled["status"] == "cancelled"
        assert [entry["revision"] for entry in registry.benchmark_review_assignment_history(payload["assignment_id"])] == [1, 2, 3]


def test_revoked_reviewer_cannot_accept_existing_assignment():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(payload, actor="curator", reason="assign")
        registry.revoke_benchmark_principal(
            "editor:lead", actor="test-bootstrap", reason="Reviewer access withdrawn",
        )
        with pytest.raises(RegistryIntegrityError, match="active registered principal"):
            registry.transition_benchmark_review_assignment(
                payload["assignment_id"], event="accept", actor="editor:lead",
                at="2026-11-20T01:00:00Z", reason="revoked reviewer attempt",
            )


def test_assignment_submission_requires_stored_review_for_same_reviewer_and_artifact():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(payload, actor="curator", reason="assign")
        registry.transition_benchmark_review_assignment(
            payload["assignment_id"], event="accept", actor="editor:lead",
            at="2026-11-20T01:00:00Z", reason="accepted",
        )
        with pytest.raises(RegistryIntegrityError, match="stored human review"):
            registry.transition_benchmark_review_assignment(
                payload["assignment_id"], event="submit", actor="editor:lead",
                at="2026-11-20T02:00:00Z", reason="submit missing review",
                submitted_review_id="review:missing",
            )
        review = _benchmark_review_payload()
        registry.add_benchmark_review(review, actor="editor:lead", reason="complete assigned review")
        submitted = registry.transition_benchmark_review_assignment(
            payload["assignment_id"], event="submit", actor="editor:lead",
            at="2026-11-20T03:00:00Z", reason="submit actual review",
            submitted_review_id=review["review_id"],
        )
        assert submitted["status"] == "submitted"
        assert submitted["submitted_review_id"] == review["review_id"]
        assert len(registry.benchmark_review_assignment_history(payload["assignment_id"])) == 3


def test_assignment_submission_rejects_review_for_different_reviewer():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(payload, actor="curator", reason="assign")
        registry.transition_benchmark_review_assignment(
            payload["assignment_id"], event="accept", actor="editor:lead",
            at="2026-11-20T01:00:00Z", reason="accepted",
        )
        review = _benchmark_review_payload()
        review["review_id"] = "review:benchmark-en-other"
        review["reviewer"]["reviewer_id"] = "editor:second"
        registry.add_benchmark_review(review, actor="editor:second", reason="other review")
        with pytest.raises(RegistryIntegrityError, match="exact artifact"):
            registry.transition_benchmark_review_assignment(
                payload["assignment_id"], event="submit", actor="editor:lead",
                at="2026-11-20T03:00:00Z", reason="wrong reviewer",
                submitted_review_id=review["review_id"],
            )


def test_schema_v16_migrates_to_v17_assignment_ledger(tmp_path, monkeypatch):
    database = tmp_path / "registry-v16.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 16)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 16
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 17)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 17
        tables = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "benchmark_review_assignments" in tables
        assert "benchmark_review_assignment_revisions" in tables
        columns = {
            row["name"] for row in migrated._connection.execute(
                "PRAGMA table_info(benchmark_principals)"
            )
        }
        assert "languages_json" in columns
        triggers = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "benchmark_review_assignment_initial_revision" in triggers
        assert "benchmark_review_assignment_update_requires_revision" in triggers
        migrated.migrate()
        assert migrated.schema_version() == 17


def test_schema_17_migrates_principals_to_unverified_classification(tmp_path, monkeypatch):
    database = tmp_path / "registry-v17.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 17)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 17
        old_registry._connection.execute(
            """INSERT INTO benchmark_principals
            (principal_id, display_name, roles_json, active, created_at, created_by, reason, languages_json)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
            ("legacy:reviewer", "Legacy reviewer", '["benchmark_reviewer"]',
             STAMP, "legacy-bootstrap", "Pre-migration reviewer", '["en"]'),
        )
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 18)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 18
        assert migrated.get_benchmark_principal("legacy:reviewer")["principal_kind"] == "unverified"
        trigger = migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'benchmark_principal_kind_immutable'"
        ).fetchone()
        assert trigger is not None
        with pytest.raises(sqlite3.IntegrityError, match="classification is immutable"):
            migrated._connection.execute(
                "UPDATE benchmark_principals SET principal_kind = 'human' WHERE principal_id = 'legacy:reviewer'"
            )
        migrated.migrate()
        assert migrated.schema_version() == 18


def test_schema_18_migrates_to_19_identity_attestation_ledger(tmp_path, monkeypatch):
    database = tmp_path / "registry-v18.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 18)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 18
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 19)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 19
        tables = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "benchmark_review_identity_attestations" in tables
        triggers = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "benchmark_review_identity_attestations_no_update" in triggers
        assert "benchmark_review_identity_attestations_no_delete" in triggers
        migrated.migrate()
        assert migrated.schema_version() == 19


def _identity_attestation(review: dict, actor: str) -> dict:
    return {
        "attestation_id": f"identity-attestation:{review['review_id']}:1",
        "review_id": review["review_id"],
        "review_revision": 1,
        "principal_id": actor,
        "issuer": "https://login.example.test/",
        "audience": "sngso-benchmark",
        "subject": f"oidc:{actor}",
        "authenticated_at": "2026-11-20T00:00:00Z",
        "expires_at": "2026-11-20T01:00:00Z",
        "authentication_methods": ["pwd", "mfa"],
        "authentication_context": "urn:example:loa:2",
        "provider_key_id": "key-2026-01",
        "provider_adapter": "example-oidc-adapter/1.0",
        "session_fingerprint": "sha256:" + "b" * 64,
        "attested_at": "2026-11-20T00:01:00Z",
    }


def test_benchmark_readiness_never_confuses_test_principals_with_verified_humans():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="fixture review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Independent editorial experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second fixture review")
        report = registry.benchmark_readiness_report(first["benchmark_id"])
        assert report["status"] == "human_review_pending"
        assert report["eligible_approved_review_count"] == 2
        assert report["reviewer_classifications"] == ["unverified"]
        assert report["production_ready"] is False
        assert any("identity-provider attestation" in item for item in report["blockers"])


def test_benchmark_readiness_requires_adjudication_even_for_two_human_classified_reviewers():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry, reviewer_kind="human")
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="human-classified review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Independent editorial experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second human-classified review")
        report = registry.benchmark_readiness_report(first["benchmark_id"])
        for review in (first, second):
            registry.record_benchmark_review_identity_attestation(
                _identity_attestation(review, review["reviewer"]["reviewer_id"]),
                actor=review["reviewer"]["reviewer_id"],
                reason="test trusted provider assertion",
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity attestations are immutable"):
            registry._connection.execute(
                "UPDATE benchmark_review_identity_attestations SET subject = 'forged' WHERE review_id = ?",
                (first["review_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity attestations are immutable"):
            registry._connection.execute(
                "DELETE FROM benchmark_review_identity_attestations WHERE review_id = ?",
                (first["review_id"],),
            )
        report = registry.benchmark_readiness_report(first["benchmark_id"])
        assert report["status"] == "ready_for_adjudication"
        assert report["distinct_eligible_reviewer_count"] == 2
        assert report["production_ready"] is False
        assert report["blockers"] == ["No independent final adjudication is recorded."]


def test_identity_attestation_must_be_fresh_and_match_current_review_revision():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry, reviewer_kind="human")
        review = _benchmark_review_payload()
        registry.add_benchmark_review(review, actor="editor:lead", reason="initial review")
        stale = _identity_attestation(review, "editor:lead")
        stale["attested_at"] = "2026-11-20T00:20:00Z"
        with pytest.raises(RegistryIntegrityError, match="within 15 minutes"):
            registry.record_benchmark_review_identity_attestation(
                stale, actor="editor:lead", reason="stale identity assertion",
            )
        valid = _identity_attestation(review, "editor:lead")
        registry.record_benchmark_review_identity_attestation(
            valid, actor="editor:lead", reason="fresh identity assertion",
        )
        revised = {**review, "adjudication_note": "Reviewed again with a clearer rationale."}
        registry.revise_benchmark_review(
            review["review_id"], revised, expected_revision=1,
            actor="editor:lead", reason="revised review rationale",
        )
        report = registry.benchmark_readiness_report(review["benchmark_id"])
        assert report["reviews"][0]["identity_provider_attested"] is False
        assert report["status"] == "human_review_pending"


def test_synthetic_only_benchmark_is_never_reported_as_human_ready():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry, reviewer_kind="synthetic")
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="synthetic fixture review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Synthetic test fixture reviewer with no verified human identity."
        registry.add_benchmark_review(second, actor="editor:second", reason="second synthetic fixture review")
        report = registry.benchmark_readiness_report(first["benchmark_id"])
        assert report["status"] == "synthetic_only"
        assert report["production_ready"] is False


def test_benchmark_review_persists_with_registered_source_references_and_history():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        saved = registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")
        assert saved["revision"] == 1
        assert registry.get_benchmark_review(payload["review_id"])["status"] == "approved"
        history = registry.benchmark_review_history(payload["review_id"])
        assert len(history) == 1
        assert history[0]["actor"] == "editor:lead"
        assert history[0]["reason"] == "initial review"


def test_benchmark_review_revision_is_append_only_and_optimistically_locked():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")
        revised = {**payload, "status": "rejected", "adjudication_note": "Source attribution needs correction."}
        revised["reviewer"]["reviewer_id"] = "editor:second"
        result = registry.revise_benchmark_review(
            payload["review_id"], revised, expected_revision=1,
            actor="editor:second", reason="independent challenge",
        )
        assert result["revision"] == 2
        history = registry.benchmark_review_history(payload["review_id"])
        assert [item["revision"] for item in history] == [1, 2]
        assert history[1]["actor"] == "editor:second"
        with pytest.raises(RegistryIntegrityError, match="revision conflict"):
            registry.revise_benchmark_review(
                payload["review_id"], revised, expected_revision=1,
                actor="editor:second", reason="stale update",
            )


def test_benchmark_review_rejects_unregistered_or_mismatched_source_material():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        payload["source_materials"][0]["source_id"] = "source:unknown"
        with pytest.raises(RegistryIntegrityError, match="registered article"):
            registry.add_benchmark_review(payload, actor="editor:lead", reason="bad source")


def test_benchmark_review_requires_exact_operation_and_endpoint_grant():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        payload["source_materials"][0]["permission_operation"] = "translation"
        with pytest.raises(RegistryIntegrityError, match="does not explicitly authorize"):
            registry.add_benchmark_review(payload, actor="editor:lead", reason="operation not covered")

    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        payload["source_materials"][0]["endpoint_id"] = "endpoint:publisher-b-rss"
        with pytest.raises(RegistryIntegrityError, match="does not cover the declared endpoint"):
            registry.add_benchmark_review(payload, actor="editor:lead", reason="endpoint not covered")


def test_benchmark_review_quality_report_rechecks_current_permission():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")

        report = registry.benchmark_review_quality_report(payload["review_id"])
        assert report["eligible_for_current_use"] is True
        assert all(item["passed"] for item in report["checks"])

        registry.transition_permission_grant(
            "permission:publisher-a-discovery-001",
            "suspended",
            actor="rights-reviewer",
            reason="Permission requires renewed confirmation.",
        )
        report = registry.benchmark_review_quality_report(payload["review_id"])
        assert report["eligible_for_current_use"] is False
        assert any(
            item["name"] == "current_source_permission" and not item["passed"]
            for item in report["checks"]
        )


def test_story_snapshot_includes_linked_benchmark_review_provenance():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        registry.add_domain_record("story", story_record())
        payload = _benchmark_review_payload()
        registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")

        snapshot = registry.story_editorial_snapshot("story:sample-event")
        assert len(snapshot["benchmark_reviews"]) == 1
        item = snapshot["benchmark_reviews"][0]
        assert item["review_id"] == payload["review_id"]
        assert item["revision"] == 1
        assert item["quality_report"]["eligible_for_current_use"] is True
        assert item["history"][0]["actor"] == "editor:lead"

        registry.transition_permission_grant(
            "permission:publisher-a-discovery-001",
            "suspended",
            actor="rights-reviewer",
            reason="Permission requires renewed confirmation.",
        )
        refreshed = registry.story_editorial_snapshot("story:sample-event")
        assert refreshed["benchmark_reviews"][0]["quality_report"]["eligible_for_current_use"] is False


def test_benchmark_review_history_cannot_be_updated_or_deleted():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM benchmark_review_revisions WHERE review_id = ?",
                (payload["review_id"],),
            )


def test_existing_schema_v13_database_migrates_to_v14(tmp_path, monkeypatch):
    database = tmp_path / "registry-v13.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 13)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 13
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'benchmark_artifacts'"
        ).fetchone() is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 14)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 14
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'benchmark_artifacts_no_update'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 14


def test_benchmark_artifact_fingerprint_is_computed_from_canonical_content():
    with SQLiteRegistry() as registry:
        _register_benchmark_test_principals(registry)
        first = registry.register_benchmark_artifact(
            "benchmark:canonical-en-v1", language="en",
            content={"schema_version": "1.0.0", "items": [{"item_id": "canonical-1", "task_type": "summary", "input": {"z": 2, "a": [1]}, "expected_output": "Short accurate summary", "scoring_criteria": ["Faithful"]}]},
            actor="curator", reason="initial registration",
        )
        second = registry.get_benchmark_artifact("benchmark:canonical-en-v1")
        assert first["content_fingerprint"] == registry_module.sha256_fingerprint(
            {"schema_version": "1.0.0", "items": [{"item_id": "canonical-1", "task_type": "summary", "input": {"a": [1], "z": 2}, "expected_output": "Short accurate summary", "scoring_criteria": ["Faithful"]}]}
        )
        assert second["fingerprint_verified"] is True
        assert second["content"] == {"schema_version": "1.0.0", "items": [{"item_id": "canonical-1", "task_type": "summary", "input": {"a": [1], "z": 2}, "expected_output": "Short accurate summary", "scoring_criteria": ["Faithful"]}]}


def test_benchmark_artifact_rejects_unstructured_or_incomplete_items():
    with SQLiteRegistry() as registry:
        with pytest.raises(RegistryIntegrityError, match="canonical editorial benchmark contract"):
            registry.register_benchmark_artifact(
                "benchmark:invalid-en-v1", language="en",
                content={"items": [{"prompt": "Only a prompt"}]},
                actor="curator", reason="invalid item fixture",
            )


def test_benchmark_artifact_rejects_duplicate_item_ids():
    with SQLiteRegistry() as registry:
        _register_benchmark_test_principals(registry)
        item = {
            "item_id": "duplicate-1",
            "task_type": "headline",
            "input": "Input",
            "expected_output": "Output",
            "scoring_criteria": ["Accurate"],
        }
        with pytest.raises(RegistryIntegrityError, match="item IDs must be unique"):
            registry.register_benchmark_artifact(
                "benchmark:duplicate-en-v1", language="en",
                content={"schema_version": "1.0.0", "items": [item, dict(item)]},
                actor="curator", reason="duplicate fixture",
            )


def test_benchmark_artifacts_cannot_be_updated_or_deleted():
    with SQLiteRegistry() as registry:
        _register_benchmark_test_principals(registry)
        artifact = registry.register_benchmark_artifact(
            "benchmark:immutable-en-v1", language="en",
            content={"schema_version": "1.0.0", "items": [{"item_id": "item-1", "task_type": "headline", "input": "Input", "expected_output": "Output", "scoring_criteria": ["Accurate"]}]}, actor="curator", reason="initial registration",
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "UPDATE benchmark_artifacts SET reason = 'tampered' WHERE artifact_id = ?",
                (artifact["artifact_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "DELETE FROM benchmark_artifacts WHERE artifact_id = ?",
                (artifact["artifact_id"],),
            )


def test_benchmark_review_requires_exact_registered_artifact_and_language():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        payload["benchmark_fingerprint"] = "sha256:" + "f" * 64
        with pytest.raises(RegistryIntegrityError, match="does not match registered artifact"):
            registry.add_benchmark_review(payload, actor="editor:lead", reason="wrong fingerprint")

    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        payload["language"] = "bn"
        payload["reviewer"]["language"] = "bn"
        with pytest.raises(RegistryIntegrityError, match="language does not match"):
            registry.add_benchmark_review(payload, actor="editor:lead", reason="wrong language")


def test_existing_schema_v12_database_migrates_to_v13(tmp_path, monkeypatch):
    database = tmp_path / "registry-v12.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 12)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 12
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'benchmark_reviews'"
        ).fetchone() is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 13)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 13
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'benchmark_reviews_record_update'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 13


def test_direct_review_head_update_also_creates_history_via_trigger():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")
        changed = {**payload, "status": "rejected", "adjudication_note": "Second review required."}
        registry._connection.execute(
            """UPDATE benchmark_reviews
            SET status = 'rejected', revision = 2, updated_at = ?,
                last_actor = ?, last_reason = ?, payload_json = ?
            WHERE review_id = ?""",
            (registry_module._utc_now(), "editor:second", "direct SQL revision", __import__("json").dumps(changed), payload["review_id"]),
        )
        history = registry.benchmark_review_history(payload["review_id"])
        assert [item["revision"] for item in history] == [1, 2]
        assert history[1]["actor"] == "editor:second"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE benchmark_review_revisions SET reason = 'tampered' WHERE review_id = ? AND revision = 1",
                (payload["review_id"],),
            )


def test_benchmark_review_cannot_be_rebound_to_a_different_fingerprint():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_review_payload()
        registry.add_benchmark_review(payload, actor="editor:lead", reason="initial review")
        changed = {**payload, "benchmark_fingerprint": "sha256:" + "e" * 64}
        with pytest.raises(RegistryIntegrityError, match="does not match registered artifact"):
            registry.revise_benchmark_review(
                payload["review_id"], changed, expected_revision=1,
                actor="editor:second", reason="attempt to rebind old review",
            )


def test_benchmark_principal_roles_and_revocation_are_enforced_for_curators():
    with SQLiteRegistry() as registry:
        registry.register_benchmark_principal(
            "reviewer-only", display_name="Reviewer only",
            roles=["benchmark_reviewer"], provisioned_by="trusted-bootstrap",
            reason="Test role boundary",
        )
        content = {"schema_version": "1.0.0", "items": [{
            "item_id": "role-test", "task_type": "headline", "input": "Input",
            "expected_output": "Output", "scoring_criteria": ["Accurate"],
        }]}
        with pytest.raises(RegistryIntegrityError, match="lacks required role"):
            registry.register_benchmark_artifact(
                "benchmark:role-denied", language="en", content=content,
                actor="reviewer-only", reason="Must be denied",
            )
        registry.register_benchmark_principal(
            "curator-active", display_name="Curator",
            roles=["benchmark_curator"], provisioned_by="trusted-bootstrap",
            reason="Test curator role",
        )
        registry.revoke_benchmark_principal(
            "curator-active", actor="trusted-bootstrap", reason="Access withdrawn",
        )
        assert registry.get_benchmark_principal("curator-active")["active"] is False
        with pytest.raises(RegistryIntegrityError, match="active registered principal"):
            registry.register_benchmark_artifact(
                "benchmark:revoked", language="en", content=content,
                actor="curator-active", reason="Must be denied after revocation",
            )


def test_benchmark_principal_role_assignments_are_validated_and_audited():
    with SQLiteRegistry() as registry:
        with pytest.raises(ValueError, match="roles must be"):
            registry.register_benchmark_principal(
                "unknown-role", display_name="Unknown",
                roles=["superuser"], provisioned_by="trusted-bootstrap",
                reason="Invalid role",
            )
        principal = registry.register_benchmark_principal(
            "curator-one", display_name="Curator One",
            roles=["benchmark_curator"], provisioned_by="trusted-bootstrap",
            reason="Approved curator",
        )
        assert principal["active"] is True
        assert principal["roles"] == ["benchmark_curator"]
        events = registry.audit_events(entity_id="curator-one")
        assert [event["event_type"] for event in events] == ["benchmark_principal.provisioned"]


def test_existing_schema_v14_database_migrates_to_v15(tmp_path, monkeypatch):
    database = tmp_path / "registry-v14.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 14)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 14
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'benchmark_principals'"
        ).fetchone() is None
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 15)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 15
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'benchmark_adjudications'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 15


def test_benchmark_adjudication_is_persisted_against_two_stored_reviews():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="first independent review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Experienced English editor with independent verification experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second independent review")
        adjudication = {
            "schema_version": "1.0.0",
            "adjudication_id": "adjudication:benchmark-en-001",
            "benchmark_id": first["benchmark_id"],
            "benchmark_fingerprint": first["benchmark_fingerprint"],
            "review_ids": [first["review_id"], second["review_id"]],
            "reviewer_ids": ["editor:lead", "editor:second"],
            "adjudicator": {
                "principal_id": "adjudicator:senior",
                "role": "benchmark_adjudicator",
                "qualification_basis": "Senior bilingual editor with documented adjudication experience.",
                "independent_of_reviewers": True,
            },
            "outcome": "split_accepted_answers",
            "rationale": "Both answers preserve the verified facts while representing distinct acceptable editorial choices.",
            "evidence_references": ["permission-evidence:publisher-a-001"],
            "resolved_at": "2026-11-20T00:00:00Z",
            "issue_resolutions": [{
                "issue_id": "claim-support-1",
                "category": "source_support",
                "severity": "major",
                "description": "The reviewers disagree about whether the source supports the benchmark answer.",
                "reviewer_positions": {
                    first["review_id"]: "The cited source directly supports the answer.",
                    second["review_id"]: "The cited source does not establish the answer as written."
                },
                "disposition": "reviewer_one_upheld",
                "resolution": "The registered primary-source evidence supports the answer, while the alternate reading omits the relevant source context.",
                "evidence_references": ["permission-evidence:publisher-a-001"]
            }],
        }
        saved = registry.add_benchmark_adjudication(
            adjudication, actor="adjudicator:senior", reason="Resolve reviewer disagreement",
        )
        assert saved["adjudication_id"] == adjudication["adjudication_id"]
        assert saved["outcome"] == "split_accepted_answers"
        assert saved["actor"] == "adjudicator:senior"
        assert registry.audit_events(entity_id=adjudication["adjudication_id"])[0]["event_type"] == "benchmark_adjudication.recorded"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._connection.execute(
                "DELETE FROM benchmark_adjudications WHERE adjudication_id = ?",
                (adjudication["adjudication_id"],),
            )


def test_benchmark_adjudication_rejects_reviewers_and_wrong_artifact_binding():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="first review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        registry.add_benchmark_review(second, actor="editor:second", reason="second review")
        payload = {
            "schema_version": "1.0.0",
            "adjudication_id": "adjudication:benchmark-en-bad",
            "benchmark_id": first["benchmark_id"],
            "benchmark_fingerprint": first["benchmark_fingerprint"],
            "review_ids": [first["review_id"], second["review_id"]],
            "reviewer_ids": ["editor:lead", "editor:second"],
            "adjudicator": {
                "principal_id": "adjudicator:senior",
                "role": "benchmark_adjudicator",
                "qualification_basis": "Senior bilingual editor with documented adjudication experience.",
                "independent_of_reviewers": True,
            },
            "outcome": "uphold_existing",
            "rationale": "The first review is supported by the registered source evidence and the second review is resolved.",
            "evidence_references": ["permission-evidence:publisher-a-001"],
            "resolved_at": "2026-11-20T00:00:00Z",
            "issue_resolutions": [{
                "issue_id": "claim-support-1",
                "category": "source_support",
                "severity": "major",
                "description": "The reviewers disagree about whether the source supports the benchmark answer.",
                "reviewer_positions": {
                    first["review_id"]: "The cited source directly supports the answer.",
                    second["review_id"]: "The cited source does not establish the answer as written."
                },
                "disposition": "reviewer_one_upheld",
                "resolution": "The registered primary-source evidence supports the answer, while the alternate reading omits the relevant source context.",
                "evidence_references": ["permission-evidence:publisher-a-001"]
            }],
        }
        payload["reviewer_ids"] = ["editor:lead", "editor:second"]
        payload["adjudicator"]["principal_id"] = "editor:lead"
        with pytest.raises(RegistryIntegrityError, match="contract or independence|lacks required role|independent"):
            registry.add_benchmark_adjudication(
                payload, actor="editor:lead", reason="Invalid self-adjudication",
            )


@pytest.mark.parametrize(
    ("outcome", "expected_eligible"),
    [
        ("uphold_existing", True),
        ("return_for_revision", False),
        ("reject_benchmark_item", False),
        ("split_accepted_answers", False),
    ],
)
def test_adjudication_outcome_controls_current_use_eligibility(outcome, expected_eligible):
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="first review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Experienced English editor with independent verification experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second review")
        adjudication = {
            "schema_version": "1.0.0",
            "adjudication_id": f"adjudication:policy-{outcome}",
            "benchmark_id": first["benchmark_id"],
            "benchmark_fingerprint": first["benchmark_fingerprint"],
            "review_ids": [first["review_id"], second["review_id"]],
            "reviewer_ids": ["editor:lead", "editor:second"],
            "adjudicator": {
                "principal_id": "adjudicator:senior",
                "role": "benchmark_adjudicator",
                "qualification_basis": "Senior bilingual editor with documented adjudication experience.",
                "independent_of_reviewers": True,
            },
            "outcome": outcome,
            "rationale": "The adjudicator compared both independent reviews against the registered evidence and resolved their disagreement.",
            "evidence_references": ["permission-evidence:publisher-a-001"],
            "resolved_at": "2026-11-20T00:00:00Z",
            "issue_resolutions": [{
                "issue_id": "claim-support-1",
                "category": "source_support",
                "severity": "major",
                "description": "The reviewers disagree about whether the source supports the benchmark answer.",
                "reviewer_positions": {
                    first["review_id"]: "The cited source directly supports the answer.",
                    second["review_id"]: "The cited source does not establish the answer as written."
                },
                "disposition": "reviewer_one_upheld",
                "resolution": "The registered primary-source evidence supports the answer, while the alternate reading omits the relevant source context.",
                "evidence_references": ["permission-evidence:publisher-a-001"]
            }],
        }
        if outcome == "uphold_existing":
            adjudication["upheld_review_ids"] = [first["review_id"]]
        registry.add_benchmark_adjudication(
            adjudication, actor="adjudicator:senior", reason="Apply adjudication policy",
        )
        report = registry.benchmark_review_quality_report(first["review_id"])
        assert report["eligible_for_current_use"] is expected_eligible
        policy_check = next(item for item in report["checks"] if item["name"] == "adjudication_policy")
        assert policy_check["passed"] is expected_eligible
        assert report["adjudication"]["outcome"] == outcome


def test_benchmark_adjudication_rejects_multiple_final_decisions_for_one_artifact():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="first review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Experienced English editor with independent verification experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second review")
        payload = {
            "schema_version": "1.0.0",
            "adjudication_id": "adjudication:final-one",
            "benchmark_id": first["benchmark_id"],
            "benchmark_fingerprint": first["benchmark_fingerprint"],
            "review_ids": [first["review_id"], second["review_id"]],
            "reviewer_ids": ["editor:lead", "editor:second"],
            "adjudicator": {
                "principal_id": "adjudicator:senior",
                "role": "benchmark_adjudicator",
                "qualification_basis": "Senior bilingual editor with documented adjudication experience.",
                "independent_of_reviewers": True,
            },
            "outcome": "reject_benchmark_item",
            "rationale": "The benchmark item contains a material ambiguity that cannot be accepted in its current form.",
            "evidence_references": ["permission-evidence:publisher-a-001"],
            "resolved_at": "2026-11-20T00:00:00Z",
            "issue_resolutions": [{
                "issue_id": "claim-support-1",
                "category": "source_support",
                "severity": "major",
                "description": "The reviewers disagree about whether the source supports the benchmark answer.",
                "reviewer_positions": {
                    first["review_id"]: "The cited source directly supports the answer.",
                    second["review_id"]: "The cited source does not establish the answer as written."
                },
                "disposition": "reviewer_one_upheld",
                "resolution": "The registered primary-source evidence supports the answer, while the alternate reading omits the relevant source context.",
                "evidence_references": ["permission-evidence:publisher-a-001"]
            }],
        }
        registry.add_benchmark_adjudication(payload, actor="adjudicator:senior", reason="Final decision")
        second_decision = dict(payload)
        second_decision["adjudication_id"] = "adjudication:final-two"
        second_decision["outcome"] = "uphold_existing"
        second_decision["upheld_review_ids"] = [first["review_id"]]
        second_decision["resolved_at"] = "2026-11-20T00:01:00Z"
        with pytest.raises(sqlite3.IntegrityError, match="already has a final adjudication"):
            registry.add_benchmark_adjudication(second_decision, actor="adjudicator:senior", reason="Attempt to supersede final decision")
        report = registry.benchmark_review_quality_report(first["review_id"])
        assert report["eligible_for_current_use"] is False
        assert report["adjudication"]["outcome"] == "reject_benchmark_item"


def test_final_adjudication_freezes_review_revision_for_that_artifact():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="first review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Experienced English editor with independent verification experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second review")
        adjudication = {
            "schema_version": "1.0.0",
            "adjudication_id": "adjudication:freeze-review",
            "benchmark_id": first["benchmark_id"],
            "benchmark_fingerprint": first["benchmark_fingerprint"],
            "review_ids": [first["review_id"], second["review_id"]],
            "reviewer_ids": ["editor:lead", "editor:second"],
            "adjudicator": {
                "principal_id": "adjudicator:senior",
                "role": "benchmark_adjudicator",
                "qualification_basis": "Senior bilingual editor with documented adjudication experience.",
                "independent_of_reviewers": True,
            },
            "outcome": "reject_benchmark_item",
            "rationale": "The benchmark item is rejected because its expected output cannot be justified by the supplied source evidence.",
            "evidence_references": ["permission-evidence:publisher-a-001"],
            "resolved_at": "2026-11-20T00:00:00Z",
            "issue_resolutions": [{
                "issue_id": "claim-support-1",
                "category": "source_support",
                "severity": "major",
                "description": "The reviewers disagree about whether the source supports the benchmark answer.",
                "reviewer_positions": {
                    first["review_id"]: "The cited source directly supports the answer.",
                    second["review_id"]: "The cited source does not establish the answer as written."
                },
                "disposition": "reviewer_one_upheld",
                "resolution": "The registered primary-source evidence supports the answer, while the alternate reading omits the relevant source context.",
                "evidence_references": ["permission-evidence:publisher-a-001"]
            }],
        }
        registry.add_benchmark_adjudication(adjudication, actor="adjudicator:senior", reason="Final rejection")
        revised = dict(first)
        revised["status"] = "rejected"
        revised["adjudication_note"] = "The adjudicator rejected this item."
        with pytest.raises(RegistryIntegrityError, match="finally adjudicated"):
            registry.revise_benchmark_review(
                first["review_id"], revised, expected_revision=1,
                actor="editor:lead", reason="Attempt revision after final adjudication",
            )


def test_uphold_existing_only_preserves_the_explicitly_selected_approved_review():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        first = _benchmark_review_payload()
        registry.add_benchmark_review(first, actor="editor:lead", reason="first review")
        second = _benchmark_review_payload()
        second["review_id"] = "review:benchmark-en-002"
        second["reviewer"]["reviewer_id"] = "editor:second"
        second["reviewer"]["qualification_basis"] = "Experienced English editor with independent verification experience."
        registry.add_benchmark_review(second, actor="editor:second", reason="second review")
        payload = {
            "schema_version": "1.0.0",
            "adjudication_id": "adjudication:select-one",
            "benchmark_id": first["benchmark_id"],
            "benchmark_fingerprint": first["benchmark_fingerprint"],
            "review_ids": [first["review_id"], second["review_id"]],
            "reviewer_ids": ["editor:lead", "editor:second"],
            "adjudicator": {
                "principal_id": "adjudicator:senior",
                "role": "benchmark_adjudicator",
                "qualification_basis": "Senior bilingual editor with documented adjudication experience.",
                "independent_of_reviewers": True,
            },
            "outcome": "uphold_existing",
            "upheld_review_ids": [first["review_id"]],
            "rationale": "The first approved review correctly applies the evidence while the second review is not accepted.",
            "evidence_references": ["permission-evidence:publisher-a-001"],
            "resolved_at": "2026-11-20T00:00:00Z",
            "issue_resolutions": [{
                "issue_id": "claim-support-1",
                "category": "source_support",
                "severity": "major",
                "description": "The reviewers disagree about whether the source supports the benchmark answer.",
                "reviewer_positions": {
                    first["review_id"]: "The cited source directly supports the answer.",
                    second["review_id"]: "The cited source does not establish the answer as written."
                },
                "disposition": "reviewer_one_upheld",
                "resolution": "The registered primary-source evidence supports the answer, while the alternate reading omits the relevant source context.",
                "evidence_references": ["permission-evidence:publisher-a-001"]
            }],
        }
        registry.add_benchmark_adjudication(payload, actor="adjudicator:senior", reason="Select accepted review")
        assert registry.benchmark_review_quality_report(first["review_id"])["eligible_for_current_use"] is True
        assert registry.benchmark_review_quality_report(second["review_id"])["eligible_for_current_use"] is False


def test_schema_v15_migrates_to_v16_with_single_final_adjudication_guard(tmp_path, monkeypatch):
    database = tmp_path / "registry-v15.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 15)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 15
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 16)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 16
        trigger = migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'benchmark_adjudications_one_per_artifact'"
        ).fetchone()
        assert trigger is not None
        migrated.migrate()
        assert migrated.schema_version() == 16





def test_latest_registry_contains_append_only_score_study_execution_and_acceptance_stores():
    with SQLiteRegistry() as registry:
        assert registry.schema_version() == 35
        tables = {
            row["name"] for row in registry._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        triggers = {
            row["name"] for row in registry._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "edition_artifacts" in tables
        assert "benchmark_scores" in tables
        assert "edition_artifacts_no_update" in triggers
        assert "edition_artifacts_no_delete" in triggers
        assert "benchmark_scores_no_update" in triggers
        assert "benchmark_scores_no_delete" in triggers
        assert "benchmark_study_protocols" in tables
        assert "benchmark_study_protocols_no_update" in triggers
        assert "benchmark_study_protocols_no_delete" in triggers
        assert "benchmark_study_executions" in tables
        assert "benchmark_study_executions_no_update" in triggers
        assert "benchmark_study_executions_no_delete" in triggers
        assert "benchmark_acceptance_reviews" in tables
        assert "benchmark_acceptance_reviews_no_update" in triggers
        assert "benchmark_acceptance_reviews_no_delete" in triggers


def test_existing_schema_v20_database_migrates_to_v21(tmp_path, monkeypatch):
    database = tmp_path / "registry-v20.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 20)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 20
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_study_protocols'"
        ).fetchone() is None
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 21)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 21
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_study_protocols'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 21


def test_existing_schema_v21_database_migrates_to_v23(tmp_path, monkeypatch):
    database = tmp_path / "registry-v21.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 21)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 21
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_study_executions'"
        ).fetchone() is None
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 23)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 23
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_study_executions'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 23


def test_existing_schema_v24_database_migrates_to_v25(tmp_path, monkeypatch):
    database = tmp_path / "registry-v24.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 24)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 24
        trigger = old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='learning_object_question_answer_evidence_insert'"
        ).fetchone()
        assert trigger is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 25)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 25
        trigger_names = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "learning_object_question_answer_evidence_insert" in trigger_names
        assert "learning_object_question_answer_evidence_update" in trigger_names
        migrated.migrate()
        assert migrated.schema_version() == 25


def test_existing_schema_v23_database_migrates_to_v24(tmp_path, monkeypatch):
    database = tmp_path / "registry-v23.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 23)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 23
        trigger = old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='learning_object_vocabulary_source_evidence_insert'"
        ).fetchone()
        assert trigger is None

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 24)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 24
        trigger_names = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "learning_object_vocabulary_source_evidence_insert" in trigger_names
        assert "learning_object_vocabulary_source_evidence_update" in trigger_names
        migrated.migrate()
        assert migrated.schema_version() == 24


def test_existing_schema_v22_database_migrates_to_v23(tmp_path, monkeypatch):
    database = tmp_path / "registry-v22.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 22)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 22
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_study_executions'"
        ).fetchone() is not None
        assert old_registry._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_acceptance_reviews'"
        ).fetchone() is None
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 23)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 23
        assert migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_acceptance_reviews'"
        ).fetchone() is not None
        migrated.migrate()
        assert migrated.schema_version() == 23


def test_schema_30_migrates_to_31_staging_channel(tmp_path, monkeypatch) -> None:
    database = tmp_path / "staging-v30.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 30)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 30
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 31)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 31
        state = migrated._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        assert dict(state) == {
            "channel": "staging", "state_revision": 0, "current_event_id": None,
            "current_export_id": None, "lkg_event_id": None, "lkg_export_id": None,
        }


def test_domain_revision_insert_must_match_the_current_head():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        with pytest.raises(sqlite3.IntegrityError, match="must match current head"):
            registry._connection.execute(
                """INSERT INTO domain_record_revisions
                   (record_type, record_id, revision, created_at, actor, reason, payload_json)
                   VALUES ('article', 'article:publisher-a-001', 2, ?, 'attacker', 'forged', ?)""",
                ("2026-11-20T00:00:00Z", '{"id":"article:publisher-a-001","forged":true}'),
            )


def test_permission_grant_revision_insert_and_head_update_are_guarded():
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        with pytest.raises(sqlite3.IntegrityError, match="current or next legal head"):
            registry._connection.execute(
                """INSERT INTO permission_grant_revisions
                   (grant_id, revision, created_at, actor, reason, payload_json)
                   VALUES (?, 3, ?, 'attacker', 'forged', ?)""",
                (
                    "permission:publisher-a-discovery-001",
                    "2026-11-20T00:00:00Z",
                    '{"id":"permission:publisher-a-discovery-001","source_id":"source:publisher-a","status":"revoked"}',
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="requires matching revision"):
            registry._connection.execute(
                """UPDATE permission_grants
                   SET status = 'revoked', revision = revision + 1,
                       payload_json = json_set(payload_json, '$.status', 'revoked')
                   WHERE id = ?""",
                ("permission:publisher-a-discovery-001",),
            )


def test_assignment_sql_boundary_rejects_illegal_status_jump():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(
            payload, actor="curator", reason="Assign independent review",
        )
        forged = dict(payload)
        forged.update(
            status="submitted",
            accepted_at="2026-11-20T01:00:00Z",
            submitted_review_id="review:forged",
            closed_at="2026-11-20T02:00:00Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="current or next legal head"):
            registry._connection.execute(
                """INSERT INTO benchmark_review_assignment_revisions
                   (assignment_id, revision, created_at, actor, reason, payload_json)
                   VALUES (?, 2, ?, 'editor:lead', 'forged submission', ?)""",
                (payload["assignment_id"], "2026-11-20T02:00:00Z", json.dumps(forged)),
            )


def test_schema_33_migrates_to_34_with_revision_and_assignment_guards(tmp_path, monkeypatch):
    database = tmp_path / "registry-v33.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 33)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 33

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 34)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 34
        triggers = {
            row["name"]
            for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {
            "domain_record_revisions_validate_insert",
            "permission_grant_revisions_validate_insert",
            "permission_grants_revision_guard",
            "benchmark_review_assignment_revisions_validate_insert",
            "benchmark_review_assignment_status_transition_guard",
        } <= triggers


def test_permission_grant_sql_transition_cannot_rewrite_scope_or_evidence():
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())
        row = registry._connection.execute(
            "SELECT revision, payload_json FROM permission_grants WHERE id = ?",
            ("permission:publisher-a-discovery-001",),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["status"] = "revoked"
        payload["endpoint_ids"] = [*payload["endpoint_ids"], "endpoint:attacker"]
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        with pytest.raises(sqlite3.IntegrityError, match="scope and evidence are immutable"):
            with registry._transaction():
                registry._connection.execute(
                    """INSERT INTO permission_grant_revisions
                       (grant_id, revision, created_at, actor, reason, payload_json)
                       VALUES (?, ?, ?, 'rights-reviewer', 'attempted scope rewrite', ?)""",
                    (
                        "permission:publisher-a-discovery-001",
                        row["revision"] + 1,
                        "2026-11-20T00:00:00Z",
                        payload_json,
                    ),
                )
                registry._connection.execute(
                    """UPDATE permission_grants
                       SET status = 'revoked', revision = ?, payload_json = ?
                       WHERE id = ? AND revision = ?""",
                    (
                        row["revision"] + 1,
                        payload_json,
                        "permission:publisher-a-discovery-001",
                        row["revision"],
                    ),
                )


def test_domain_record_direct_sql_update_appends_revision_automatically():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        row = registry._connection.execute(
            """SELECT revision, payload_json FROM domain_records
               WHERE record_type = 'story' AND id = 'story:sample-event'"""
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["status"] = "ongoing"
        payload["revision"] = 2
        payload["updated_at"] = "2026-11-21T00:00:00Z"
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        registry._connection.execute(
            """UPDATE domain_records
               SET revision = 2, updated_at = ?, payload_json = ?,
                   last_actor = 'direct-sql-reviewer',
                   last_reason = 'Verified database trigger snapshot',
                   last_revision_at = ?
               WHERE record_type = 'story' AND id = 'story:sample-event' AND revision = 1""",
            ("2026-11-21T00:00:00Z", encoded, "9999-12-31T23:59:59Z"),
        )

        history = registry.domain_record_history("story", "story:sample-event")
        assert [entry["revision"] for entry in history] == [1, 2]
        assert history[1]["actor"] == "direct-sql-reviewer"
        assert history[1]["reason"] == "Verified database trigger snapshot"
        assert history[1]["record"]["status"] == "ongoing"


def test_domain_record_sql_guard_rejects_revision_jump_and_article_rebinding():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        registry.add_domain_record("story", story_record())
        story = registry._connection.execute(
            """SELECT payload_json FROM domain_records
               WHERE record_type = 'story' AND id = 'story:sample-event'"""
        ).fetchone()
        story_payload = json.loads(story["payload_json"])
        story_payload["revision"] = 3
        with pytest.raises(sqlite3.IntegrityError, match="revision must increment by one"):
            registry._connection.execute(
                """UPDATE domain_records
                   SET revision = 3, payload_json = ?, last_actor = 'editor',
                       last_reason = 'illegal revision jump', last_revision_at = ?
                   WHERE record_type = 'story' AND id = 'story:sample-event'""",
                (json.dumps(story_payload, sort_keys=True, separators=(",", ":")),
                 "2026-11-23T00:00:00Z"),
            )

        story_payload["revision"] = 1
        with pytest.raises(sqlite3.IntegrityError, match="payload revision must match indexed revision"):
            registry._connection.execute(
                """UPDATE domain_records
                   SET revision = 2, payload_json = ?, last_actor = 'editor',
                       last_reason = 'payload revision mismatch', last_revision_at = ?
                   WHERE record_type = 'story' AND id = 'story:sample-event'""",
                (json.dumps(story_payload, sort_keys=True, separators=(",", ":")),
                 "2026-11-23T00:00:00Z"),
            )

        article = registry._connection.execute(
            """SELECT payload_json FROM domain_records
               WHERE record_type = 'article' AND id = 'article:publisher-a-001'"""
        ).fetchone()
        article_payload = json.loads(article["payload_json"])
        article_payload["source_id"] = "source:attacker"
        with pytest.raises(sqlite3.IntegrityError, match="article source and canonical identity are immutable"):
            registry._connection.execute(
                """UPDATE domain_records
                   SET revision = 2, payload_json = ?, last_actor = 'editor',
                       last_reason = 'attempted rebind', last_revision_at = ?
                   WHERE record_type = 'article' AND id = 'article:publisher-a-001'""",
                (json.dumps(article_payload, sort_keys=True, separators=(",", ":")),
                 "9999-12-31T23:59:59Z"),
            )


def test_schema_34_migrates_to_35_with_automatic_domain_revision_triggers(tmp_path, monkeypatch):
    database = tmp_path / "registry-v34.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 34)
    with SQLiteRegistry(database) as old_registry:
        assert old_registry.schema_version() == 34

    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 35)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 35
        columns = {
            row["name"]
            for row in migrated._connection.execute("PRAGMA table_info(domain_records)")
        }
        assert {"last_actor", "last_reason", "last_revision_at"} <= columns
        triggers = {
            row["name"]
            for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {
            "domain_records_revision_guard",
            "domain_records_record_update",
        } <= triggers



@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("record_type", "story"),
        ("id", "article:rewritten-identity"),
        ("created_at", "2026-11-01T00:00:00Z"),
    ],
)
def test_domain_record_sql_guard_rejects_identity_rewrites(column, value):
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        row = registry._connection.execute(
            """SELECT revision, payload_json FROM domain_records
               WHERE record_type = 'article' AND id = 'article:publisher-a-001'"""
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["updated_at"] = "2026-11-21T00:00:00Z"
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        with pytest.raises(sqlite3.IntegrityError, match="identity and creation time are immutable"):
            registry._connection.execute(
                f"""UPDATE domain_records
                    SET {column} = ?, revision = revision + 1, updated_at = ?,
                        payload_json = ?, last_actor = 'sql-reviewer',
                        last_reason = 'attempted identity rewrite',
                        last_revision_at = '2026-11-23T00:00:00Z'
                    WHERE record_type = 'article' AND id = 'article:publisher-a-001'""",
                (value, "2026-11-21T00:00:00Z", encoded),
            )


def test_domain_record_sql_guard_rejects_revision_timestamp_rollback():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())
        row = registry._connection.execute(
            """SELECT payload_json FROM domain_records
               WHERE record_type = 'article' AND id = 'article:publisher-a-001'"""
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["updated_at"] = "2026-11-21T00:00:00Z"

        with pytest.raises(sqlite3.IntegrityError, match="timestamp cannot move backwards"):
            registry._connection.execute(
                """UPDATE domain_records
                   SET revision = revision + 1, updated_at = ?, payload_json = ?,
                       last_actor = 'sql-reviewer', last_reason = 'backdated revision',
                       last_revision_at = '2000-01-01T00:00:00Z'
                   WHERE record_type = 'article' AND id = 'article:publisher-a-001'""",
                (
                    "2026-11-21T00:00:00Z",
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )


def test_domain_record_revision_history_rejects_direct_update_and_delete():
    with SQLiteRegistry() as registry:
        registry.add_source(source_record())
        registry.add_domain_record("article", article_record())

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                """UPDATE domain_record_revisions
                   SET reason = 'rewritten' WHERE record_type = 'article'
                     AND record_id = 'article:publisher-a-001' AND revision = 1"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                """DELETE FROM domain_record_revisions
                   WHERE record_type = 'article'
                     AND record_id = 'article:publisher-a-001' AND revision = 1"""
            )


def test_permission_grant_revision_history_rejects_direct_update_and_delete():
    with SQLiteRegistry() as registry:
        populate_registry(registry)
        registry.add_permission_grant(grant_record())

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                """UPDATE permission_grant_revisions
                   SET reason = 'rewritten'
                   WHERE grant_id = 'permission:publisher-a-discovery-001' AND revision = 1"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                """DELETE FROM permission_grant_revisions
                   WHERE grant_id = 'permission:publisher-a-discovery-001' AND revision = 1"""
            )


def test_staging_channel_state_is_a_singleton_and_cannot_be_forged_or_deleted():
    with SQLiteRegistry() as registry:
        state = registry._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        assert state is not None
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            registry._connection.execute(
                """INSERT INTO edition_staging_channel_state
                   (channel, state_revision, current_event_id, current_export_id, lkg_event_id, lkg_export_id)
                   VALUES ('staging', 0, NULL, NULL, NULL, NULL)"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="matching append-only event"):
            registry._connection.execute(
                """UPDATE edition_staging_channel_state
                   SET state_revision = 1 WHERE channel = 'staging'"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            registry._connection.execute(
                "DELETE FROM edition_staging_channel_state WHERE channel = 'staging'"
            )
        state_after = registry._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        assert dict(state_after) == dict(state)


def test_staging_event_direct_sql_rejects_payload_identity_mismatch():
    with SQLiteRegistry() as registry:
        payload = {
            "event_id": "payload-event-id",
            "state_revision": 1,
            "channel": "staging",
            "operation": "promote",
            "manifest_id": "manifest:unregistered",
            "edition_id": "edition:unregistered",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "a" * 64,
            "export_id": "export:unregistered",
            "export_sha256": "sha256:" + "b" * 64,
            "previous_current_export_id": None,
            "lkg_export_id": None,
            "operator_principal_id": "operator:unregistered",
            "occurred_at": "2026-11-20T00:00:00Z",
            "reason": "Direct SQL boundary test",
            "publication_authorized": False,
            "target": "staging",
        }
        with pytest.raises(sqlite3.IntegrityError, match="staging event failed"):
            registry._connection.execute(
                """INSERT INTO edition_staging_events
                   (event_id, state_revision, channel, operation, manifest_id, edition_id,
                    edition_revision, payload_sha256, export_id, export_sha256,
                    previous_current_export_id, lkg_export_id, operator_principal_id,
                    occurred_at, reason, export_package_json, payload_json)
                   VALUES (?, 1, 'staging', 'promote', ?, ?, 1, ?, ?, ?, NULL, NULL,
                           ?, ?, ?, '{}', ?)""",
                (
                    "row-event-id", payload["manifest_id"], payload["edition_id"],
                    payload["payload_sha256"], payload["export_id"], payload["export_sha256"],
                    payload["operator_principal_id"], payload["occurred_at"], payload["reason"],
                    json.dumps(payload),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_staging_events"
        ).fetchone()[0] == 0
        assert registry._connection.execute(
            "SELECT state_revision FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()[0] == 0


def test_assignment_direct_sql_head_update_cannot_skip_accepted_state():
    with SQLiteRegistry() as registry:
        _populate_benchmark_review_sources(registry)
        payload = _benchmark_assignment_payload()
        registry.create_benchmark_review_assignment(
            payload, actor="curator", reason="Assign independent review",
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid benchmark assignment status transition"):
            registry._connection.execute(
                """UPDATE benchmark_review_assignments
                   SET status = 'submitted',
                       last_actor = reviewer_id,
                       last_reason = 'direct SQL attempted to skip acceptance',
                       payload_json = json_set(
                           payload_json,
                           '$.status', 'submitted',
                           '$.last_transition.actor', reviewer_id,
                           '$.last_transition.reason', 'direct SQL attempted to skip acceptance'
                       )
                   WHERE assignment_id = ?""",
                (payload["assignment_id"],),
            )
        stored = registry.get_benchmark_review_assignment(payload["assignment_id"])
        assert stored["status"] == "assigned"
        assert stored["revision"] == 1


@pytest.mark.parametrize(
    ("column", "row_value"),
    [
        ("manifest_id", "manifest:forged"),
        ("edition_id", "edition:forged"),
        ("edition_revision", 2),
        ("payload_sha256", "sha256:" + "b" * 64),
    ],
)
def test_edition_artifact_direct_sql_rejects_manifest_identity_mismatch(column, row_value):
    with SQLiteRegistry() as registry:
        manifest = {
            "manifest_id": "manifest:expected",
            "edition_id": "edition:expected",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "a" * 64,
        }
        row = {
            **manifest,
            column: row_value,
        }
        with pytest.raises(
            sqlite3.IntegrityError,
            match="stored edition artifact columns must match manifest",
        ):
            registry._connection.execute(
                """INSERT INTO edition_artifacts
                   (manifest_id, edition_id, edition_revision, payload_sha256,
                    payload_json, manifest_json, verification_json, created_at, actor, reason)
                   VALUES (?, ?, ?, ?, '{}', ?, '{}', ?, 'sql-reviewer',
                           'attempted artifact identity mismatch')""",
                (
                    row["manifest_id"],
                    row["edition_id"],
                    row["edition_revision"],
                    row["payload_sha256"],
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                    "2026-11-23T00:00:00Z",
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_artifacts"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("decision_id", "decision:forged"),
        ("manifest_id", "manifest:forged"),
        ("edition_id", "edition:forged"),
        ("edition_revision", 2),
        ("payload_sha256", "sha256:" + "b" * 64),
        ("decision", "reject"),
        ("principal_id", "principal:forged"),
        ("decided_at", "2026-11-24T00:00:00Z"),
        ("role", "edition_release_authorizer"),
        ("scope", "controlled_release_gate"),
        ("publication_authorized", True),
    ],
)
def test_edition_decision_direct_sql_rejects_payload_identity_or_scope_mismatch(
    field, forged_value,
):
    with SQLiteRegistry() as registry:
        manifest = {
            "manifest_id": "manifest:decision-test",
            "edition_id": "edition:decision-test",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "a" * 64,
        }
        registry._connection.execute(
            """INSERT INTO edition_artifacts
               (manifest_id, edition_id, edition_revision, payload_sha256,
                payload_json, manifest_json, verification_json, created_at, actor, reason)
               VALUES (?, ?, ?, ?, '{}', ?, '{}', ?, 'sql-reviewer', 'decision guard fixture')""",
            (
                manifest["manifest_id"],
                manifest["edition_id"],
                manifest["edition_revision"],
                manifest["payload_sha256"],
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                "2026-11-23T00:00:00Z",
            ),
        )
        payload = {
            "decision_id": "decision:expected",
            **manifest,
            "decision": "approve",
            "principal_id": "principal:expected",
            "decided_at": "2026-11-23T00:00:00Z",
            "role": "edition_approver",
            "scope": "staging_candidate_review",
            "publication_authorized": False,
        }
        payload[field] = forged_value
        with pytest.raises(
            sqlite3.IntegrityError,
            match="edition decision must bind an exact artifact and active human approver",
        ):
            registry._connection.execute(
                """INSERT INTO edition_decisions
                   (decision_id, manifest_id, edition_id, edition_revision, payload_sha256,
                    decision, principal_id, decided_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "decision:expected",
                    manifest["manifest_id"],
                    manifest["edition_id"],
                    manifest["edition_revision"],
                    manifest["payload_sha256"],
                    "approve",
                    "principal:expected",
                    "2026-11-23T00:00:00Z",
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_decisions"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("event_id", "event:forged"),
        ("state_revision", 2),
        ("channel", "live"),
        ("operation", "rollback"),
        ("manifest_id", "manifest:forged"),
        ("edition_id", "edition:forged"),
        ("edition_revision", 2),
        ("payload_sha256", "sha256:" + "c" * 64),
        ("export_id", "export:forged"),
        ("export_sha256", "sha256:" + "d" * 64),
        ("previous_current_export_id", "export:previous"),
        ("lkg_export_id", "export:lkg"),
        ("operator_principal_id", "operator:forged"),
        ("occurred_at", "2026-11-24T00:00:00Z"),
        ("reason", "forged reason"),
        ("publication_authorized", True),
        ("target", "production"),
    ],
)
def test_staging_event_direct_sql_rejects_each_payload_column_mismatch(
    field, forged_value,
):
    with SQLiteRegistry() as registry:
        payload = {
            "event_id": "event:expected",
            "state_revision": 1,
            "channel": "staging",
            "operation": "promote",
            "manifest_id": "manifest:expected",
            "edition_id": "edition:expected",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "a" * 64,
            "export_id": "export:expected",
            "export_sha256": "sha256:" + "b" * 64,
            "previous_current_export_id": None,
            "lkg_export_id": None,
            "operator_principal_id": "operator:expected",
            "occurred_at": "2026-11-23T00:00:00Z",
            "reason": "Staging payload binding test",
            "publication_authorized": False,
            "target": "staging",
        }
        payload[field] = forged_value
        export_package = {
            "export_id": "export:expected",
            "manifest_id": "manifest:expected",
            "payload_sha256": "sha256:" + "a" * 64,
            "target": "staging",
            "publication_authorized": False,
        }
        with pytest.raises(
            sqlite3.IntegrityError,
            match="staging event failed",
        ):
            registry._connection.execute(
                """INSERT INTO edition_staging_events
                   (event_id, state_revision, channel, operation, manifest_id, edition_id,
                    edition_revision, payload_sha256, export_id, export_sha256,
                    previous_current_export_id, lkg_export_id, operator_principal_id,
                    occurred_at, reason, export_package_json, payload_json)
                   VALUES ('event:expected', 1, 'staging', 'promote',
                           'manifest:expected', 'edition:expected', 1, ?, 'export:expected',
                           ?, NULL, NULL, 'operator:expected', '2026-11-23T00:00:00Z',
                           'Staging payload binding test', ?, ?)""",
                (
                    "sha256:" + "a" * 64,
                    "sha256:" + "b" * 64,
                    json.dumps(export_package, sort_keys=True, separators=(",", ":")),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_staging_events"
        ).fetchone()[0] == 0
        state = registry._connection.execute(
            "SELECT state_revision FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        assert state["state_revision"] == 0


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("export_id", "export:forged"),
        ("manifest_id", "manifest:forged"),
        ("payload_sha256", "sha256:" + "c" * 64),
        ("target", "production"),
        ("publication_authorized", True),
    ],
)
def test_staging_event_direct_sql_rejects_export_package_binding_mismatch(
    field, forged_value,
):
    with SQLiteRegistry() as registry:
        payload = {
            "event_id": "event:expected",
            "state_revision": 1,
            "channel": "staging",
            "operation": "promote",
            "manifest_id": "manifest:expected",
            "edition_id": "edition:expected",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "a" * 64,
            "export_id": "export:expected",
            "export_sha256": "sha256:" + "b" * 64,
            "previous_current_export_id": None,
            "lkg_export_id": None,
            "operator_principal_id": "operator:expected",
            "occurred_at": "2026-11-23T00:00:00Z",
            "reason": "Export package binding test",
            "publication_authorized": False,
            "target": "staging",
        }
        export_package = {
            "export_id": "export:expected",
            "manifest_id": "manifest:expected",
            "payload_sha256": "sha256:" + "a" * 64,
            "target": "staging",
            "publication_authorized": False,
        }
        export_package[field] = forged_value
        with pytest.raises(
            sqlite3.IntegrityError,
            match="staging event failed",
        ):
            registry._connection.execute(
                """INSERT INTO edition_staging_events
                   (event_id, state_revision, channel, operation, manifest_id, edition_id,
                    edition_revision, payload_sha256, export_id, export_sha256,
                    previous_current_export_id, lkg_export_id, operator_principal_id,
                    occurred_at, reason, export_package_json, payload_json)
                   VALUES ('event:expected', 1, 'staging', 'promote',
                           'manifest:expected', 'edition:expected', 1, ?, 'export:expected',
                           ?, NULL, NULL, 'operator:expected', '2026-11-23T00:00:00Z',
                           'Export package binding test', ?, ?)""",
                (
                    "sha256:" + "a" * 64,
                    "sha256:" + "b" * 64,
                    json.dumps(export_package, sort_keys=True, separators=(",", ":")),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_staging_events"
        ).fetchone()[0] == 0
        assert registry._connection.execute(
            "SELECT state_revision FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("authorization_id", "authorization:forged"),
        ("manifest_id", "manifest:forged"),
        ("edition_id", "edition:forged"),
        ("edition_revision", 2),
        ("payload_sha256", "sha256:" + "c" * 64),
        ("approval_decision_id", "decision:forged"),
        ("approver_principal_id", "principal:forged"),
        ("authorizer_principal_id", "principal:forged"),
        ("authorized_at", "2026-11-24T00:00:00Z"),
        ("scope", "publication"),
        ("release_authorized", False),
        ("publication_authorized", True),
    ],
)
def test_edition_release_authorization_direct_sql_rejects_payload_binding_mismatch(
    field, forged_value,
):
    with SQLiteRegistry() as registry:
        registry.register_benchmark_principal(
            "principal:approver",
            display_name="Test approver",
            roles=["edition_approver"],
            provisioned_by="test-bootstrap",
            reason="Provision release-authorization fixture approver",
            principal_kind="human",
        )
        registry.register_benchmark_principal(
            "principal:authorizer",
            display_name="Test release authorizer",
            roles=["edition_release_authorizer"],
            provisioned_by="test-bootstrap",
            reason="Provision release-authorization fixture authorizer",
            principal_kind="human",
        )
        manifest = {
            "manifest_id": "manifest:release-auth-test",
            "edition_id": "edition:release-auth-test",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "a" * 64,
        }
        registry._connection.execute(
            """INSERT INTO edition_artifacts
               (manifest_id, edition_id, edition_revision, payload_sha256,
                payload_json, manifest_json, verification_json, created_at, actor, reason)
               VALUES (?, ?, ?, ?, '{}', ?, '{}', ?, 'sql-reviewer', 'release auth fixture')""",
            (
                manifest["manifest_id"],
                manifest["edition_id"],
                manifest["edition_revision"],
                manifest["payload_sha256"],
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                "2026-11-23T00:00:00Z",
            ),
        )
        decision = {
            "decision_id": "decision:release-auth-test",
            **manifest,
            "decision": "approve",
            "principal_id": "principal:approver",
            "decided_at": "2026-11-23T00:00:00Z",
            "role": "edition_approver",
            "scope": "staging_candidate_review",
            "publication_authorized": False,
        }
        registry._connection.execute(
            """INSERT INTO edition_decisions
               (decision_id, manifest_id, edition_id, edition_revision, payload_sha256,
                decision, principal_id, decided_at, payload_json)
               VALUES (?, ?, ?, ?, ?, 'approve', ?, ?, ?)""",
            (
                decision["decision_id"],
                manifest["manifest_id"],
                manifest["edition_id"],
                manifest["edition_revision"],
                manifest["payload_sha256"],
                "principal:approver",
                decision["decided_at"],
                json.dumps(decision, sort_keys=True, separators=(",", ":")),
            ),
        )
        payload = {
            "authorization_id": "authorization:expected",
            **manifest,
            "approval_decision_id": "decision:release-auth-test",
            "approver_principal_id": "principal:approver",
            "authorizer_principal_id": "principal:authorizer",
            "authorized_at": "2026-11-23T00:00:00Z",
            "scope": "controlled_release_gate",
            "release_authorized": True,
            "publication_authorized": False,
        }
        payload[field] = forged_value
        with pytest.raises(
            sqlite3.IntegrityError,
            match="release authorization must bind an approved artifact and independent active human authorizer",
        ):
            registry._connection.execute(
                """INSERT INTO edition_release_authorizations
                   (authorization_id, manifest_id, edition_id, edition_revision,
                    payload_sha256, approval_decision_id, approver_principal_id,
                    authorizer_principal_id, authorized_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "authorization:expected",
                    manifest["manifest_id"],
                    manifest["edition_id"],
                    manifest["edition_revision"],
                    manifest["payload_sha256"],
                    "decision:release-auth-test",
                    "principal:approver",
                    "principal:authorizer",
                    "2026-11-23T00:00:00Z",
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_release_authorizations"
        ).fetchone()[0] == 0



def _valid_staging_sql_fixture(registry):
    """Create the complete independent-human authority chain required by staging SQL."""
    for principal_id, role in (
        ("principal:staging-approver", "edition_approver"),
        ("principal:staging-authorizer", "edition_release_authorizer"),
        ("principal:staging-operator", "edition_staging_operator"),
    ):
        registry.register_benchmark_principal(
            principal_id,
            display_name=principal_id,
            roles=[role],
            provisioned_by="test-bootstrap",
            reason="Create valid staging SQL fixture",
            principal_kind="human",
        )

    manifest = {
        "manifest_id": "manifest:staging-transaction",
        "edition_id": "edition:staging-transaction",
        "edition_revision": 1,
        "payload_sha256": "sha256:" + "a" * 64,
    }
    registry._connection.execute(
        """INSERT INTO edition_artifacts
           (manifest_id, edition_id, edition_revision, payload_sha256,
            payload_json, manifest_json, verification_json, created_at, actor, reason)
           VALUES (?, ?, ?, ?, '{}', ?, '{}', ?, 'test', 'valid staging transaction fixture')""",
        (
            manifest["manifest_id"], manifest["edition_id"],
            manifest["edition_revision"], manifest["payload_sha256"],
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            "2026-11-23T00:00:00Z",
        ),
    )

    decision = {
        "decision_id": "decision:staging-transaction",
        **manifest,
        "decision": "approve",
        "principal_id": "principal:staging-approver",
        "decided_at": "2026-11-23T00:01:00Z",
        "role": "edition_approver",
        "scope": "staging_candidate_review",
        "publication_authorized": False,
    }
    registry._connection.execute(
        """INSERT INTO edition_decisions
           (decision_id, manifest_id, edition_id, edition_revision, payload_sha256,
            decision, principal_id, decided_at, payload_json)
           VALUES (?, ?, ?, ?, ?, 'approve', ?, ?, ?)""",
        (
            decision["decision_id"], manifest["manifest_id"], manifest["edition_id"],
            manifest["edition_revision"], manifest["payload_sha256"],
            decision["principal_id"], decision["decided_at"],
            json.dumps(decision, sort_keys=True, separators=(",", ":")),
        ),
    )

    authorization = {
        "authorization_id": "authorization:staging-transaction",
        **manifest,
        "approval_decision_id": decision["decision_id"],
        "approver_principal_id": decision["principal_id"],
        "authorizer_principal_id": "principal:staging-authorizer",
        "authorized_at": "2026-11-23T00:02:00Z",
        "scope": "controlled_release_gate",
        "release_authorized": True,
        "publication_authorized": False,
    }
    registry._connection.execute(
        """INSERT INTO edition_release_authorizations
           (authorization_id, manifest_id, edition_id, edition_revision,
            payload_sha256, approval_decision_id, approver_principal_id,
            authorizer_principal_id, authorized_at, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            authorization["authorization_id"], manifest["manifest_id"],
            manifest["edition_id"], manifest["edition_revision"],
            manifest["payload_sha256"], decision["decision_id"],
            decision["principal_id"], authorization["authorizer_principal_id"],
            authorization["authorized_at"],
            json.dumps(authorization, sort_keys=True, separators=(",", ":")),
        ),
    )

    event = {
        "event_id": "event:staging-transaction",
        "state_revision": 1,
        "channel": "staging",
        "operation": "promote",
        **manifest,
        "export_id": "export:staging-transaction",
        "export_sha256": "sha256:" + "b" * 64,
        "previous_current_export_id": None,
        "lkg_export_id": None,
        "operator_principal_id": "principal:staging-operator",
        "occurred_at": "2026-11-23T00:03:00Z",
        "reason": "Verify atomic staging state advancement",
        "publication_authorized": False,
        "target": "staging",
        "staging_decision": {
            "decision_id": decision["decision_id"],
            "principal_id": decision["principal_id"],
        },
        "release_authorization": {
            "authorization_id": authorization["authorization_id"],
            "authorizer_principal_id": authorization["authorizer_principal_id"],
        },
    }
    export_package = {
        "export_id": event["export_id"],
        "manifest_id": manifest["manifest_id"],
        "payload_sha256": manifest["payload_sha256"],
        "target": "staging",
        "publication_authorized": False,
    }
    return event, export_package


def _insert_valid_staging_event(registry, event, export_package):
    registry._connection.execute(
        """INSERT INTO edition_staging_events
           (event_id, state_revision, channel, operation, manifest_id, edition_id,
            edition_revision, payload_sha256, export_id, export_sha256,
            previous_current_export_id, lkg_export_id, operator_principal_id,
            occurred_at, reason, export_package_json, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event["event_id"], event["state_revision"], event["channel"],
            event["operation"], event["manifest_id"], event["edition_id"],
            event["edition_revision"], event["payload_sha256"], event["export_id"],
            event["export_sha256"], event["previous_current_export_id"],
            event["lkg_export_id"], event["operator_principal_id"],
            event["occurred_at"], event["reason"],
            json.dumps(export_package, sort_keys=True, separators=(",", ":")),
            json.dumps(event, sort_keys=True, separators=(",", ":")),
        ),
    )


def test_valid_staging_event_and_channel_state_advance_atomically():
    with SQLiteRegistry() as registry:
        event, export_package = _valid_staging_sql_fixture(registry)
        with registry._transaction():
            _insert_valid_staging_event(registry, event, export_package)
            registry._connection.execute(
                """UPDATE edition_staging_channel_state
                   SET state_revision = 1,
                       current_event_id = ?,
                       current_export_id = ?,
                       lkg_event_id = NULL,
                       lkg_export_id = NULL
                   WHERE channel = 'staging' AND state_revision = 0""",
                (event["event_id"], event["export_id"]),
            )

        stored_event = registry._connection.execute(
            "SELECT * FROM edition_staging_events WHERE event_id = ?",
            (event["event_id"],),
        ).fetchone()
        state = registry._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        assert stored_event is not None
        assert state["state_revision"] == 1
        assert state["current_event_id"] == event["event_id"]
        assert state["current_export_id"] == event["export_id"]
        assert state["lkg_event_id"] is None
        assert state["lkg_export_id"] is None


def test_staging_event_insert_rolls_back_when_state_advance_fails():
    with SQLiteRegistry() as registry:
        event, export_package = _valid_staging_sql_fixture(registry)
        with pytest.raises(sqlite3.IntegrityError, match="matching append-only event"):
            with registry._transaction():
                _insert_valid_staging_event(registry, event, export_package)
                registry._connection.execute(
                    """UPDATE edition_staging_channel_state
                       SET state_revision = 2,
                           current_event_id = ?,
                           current_export_id = ?,
                           lkg_event_id = NULL,
                           lkg_export_id = NULL
                       WHERE channel = 'staging' AND state_revision = 0""",
                    (event["event_id"], event["export_id"]),
                )

        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_staging_events WHERE event_id = ?",
            (event["event_id"],),
        ).fetchone()[0] == 0
        state = registry._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        assert state["state_revision"] == 0
        assert state["current_event_id"] is None
        assert state["current_export_id"] is None


def _staging_variant(event, *, event_id, revision, operation, export_id,
                     export_sha256, previous_export_id, lkg_export_id):
    value = json.loads(json.dumps(event))
    value.update({
        "event_id": event_id,
        "state_revision": revision,
        "operation": operation,
        "export_id": export_id,
        "export_sha256": export_sha256,
        "previous_current_export_id": previous_export_id,
        "lkg_export_id": lkg_export_id,
    })
    package = {
        "export_id": export_id,
        "manifest_id": value["manifest_id"],
        "payload_sha256": value["payload_sha256"],
        "target": "staging",
        "publication_authorized": False,
    }
    return value, package


def _commit_staging_event_and_state(registry, event, export_package):
    with registry._transaction():
        _insert_valid_staging_event(registry, event, export_package)
        cursor = registry._connection.execute(
            """UPDATE edition_staging_channel_state
               SET state_revision = ?,
                   current_event_id = ?,
                   current_export_id = ?,
                   lkg_event_id = (
                       SELECT current_event_id FROM edition_staging_channel_state
                       WHERE channel = 'staging'
                   ),
                   lkg_export_id = ?
               WHERE channel = 'staging' AND state_revision = ?""",
            (
                event["state_revision"], event["event_id"], event["export_id"],
                event["lkg_export_id"], event["state_revision"] - 1,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("staging state compare-and-swap lost")


def test_staging_rollback_requires_and_restores_a_prior_promoted_export():
    with SQLiteRegistry() as registry:
        base, _ = _valid_staging_sql_fixture(registry)
        first, first_package = _staging_variant(
            base, event_id="event:staging-first", revision=1,
            operation="promote", export_id="export:staging-first",
            export_sha256="sha256:" + "1" * 64,
            previous_export_id=None, lkg_export_id=None,
        )
        _commit_staging_event_and_state(registry, first, first_package)

        second, second_package = _staging_variant(
            base, event_id="event:staging-second", revision=2,
            operation="promote", export_id="export:staging-second",
            export_sha256="sha256:" + "2" * 64,
            previous_export_id="export:staging-first",
            lkg_export_id="export:staging-first",
        )
        _commit_staging_event_and_state(registry, second, second_package)

        rollback, rollback_package = _staging_variant(
            base, event_id="event:staging-rollback", revision=3,
            operation="rollback", export_id="export:staging-first",
            export_sha256=first["export_sha256"],
            previous_export_id="export:staging-second",
            lkg_export_id="export:staging-second",
        )
        _commit_staging_event_and_state(registry, rollback, rollback_package)

        state = registry._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        history = registry._connection.execute(
            """SELECT state_revision, operation, export_id, previous_current_export_id
               FROM edition_staging_events ORDER BY state_revision"""
        ).fetchall()
        assert [(row["state_revision"], row["operation"], row["export_id"])
                for row in history] == [
                    (1, "promote", "export:staging-first"),
                    (2, "promote", "export:staging-second"),
                    (3, "rollback", "export:staging-first"),
                ]
        assert state["state_revision"] == 3
        assert state["current_event_id"] == "event:staging-rollback"
        assert state["current_export_id"] == "export:staging-first"
        assert state["lkg_event_id"] == "event:staging-second"
        assert state["lkg_export_id"] == "export:staging-second"


def test_staging_revision_compare_and_swap_serializes_independent_connections(tmp_path):
    database = tmp_path / "staging-concurrency.sqlite"
    first_registry = SQLiteRegistry(database)
    second_registry = None
    barrier = threading.Barrier(2)
    outcomes = []

    try:
        base, _ = _valid_staging_sql_fixture(first_registry)
        second_registry = SQLiteRegistry(database)
        candidates = []
        for index, registry in enumerate((first_registry, second_registry), start=1):
            event, package = _staging_variant(
                base,
                event_id=f"event:staging-race-{index}",
                revision=1,
                operation="promote",
                export_id=f"export:staging-race-{index}",
                export_sha256="sha256:" + str(index) * 64,
                previous_export_id=None,
                lkg_export_id=None,
            )
            candidates.append((registry, event, package))

        def attempt(registry, event, package):
            barrier.wait(timeout=5)
            try:
                _commit_staging_event_and_state(registry, event, package)
            except sqlite3.IntegrityError:
                outcomes.append("rejected-stale-revision")
            except RuntimeError as exc:
                outcomes.append(str(exc))
            else:
                outcomes.append("committed")

        workers = [
            threading.Thread(target=attempt, args=candidate)
            for candidate in candidates
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        assert all(not worker.is_alive() for worker in workers)
        assert sorted(outcomes) == ["committed", "rejected-stale-revision"]

        state = first_registry._connection.execute(
            "SELECT * FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()
        persisted = first_registry._connection.execute(
            "SELECT event_id, state_revision FROM edition_staging_events"
        ).fetchall()
        assert state["state_revision"] == 1
        assert len(persisted) == 1
        assert persisted[0]["event_id"] == state["current_event_id"]
    finally:
        if second_registry is not None:
            second_registry.close()
        first_registry.close()


@pytest.mark.parametrize(
    ("path", "value", "remove"),
    [
        ("event_id", None, True),
        ("state_revision", "1", False),
        ("channel", 1, False),
        ("publication_authorized", "false", False),
        ("target", None, True),
        ("operator_principal_id", 17, False),
        ("staging_decision.decision_id", None, True),
        ("release_authorization.authorizer_principal_id", 17, False),
    ],
)
def test_staging_event_direct_sql_rejects_missing_keys_and_wrong_json_scalar_types(
    path, value, remove,
):
    with SQLiteRegistry() as registry:
        event, package = _valid_staging_sql_fixture(registry)
        payload = json.loads(json.dumps(event))
        target = payload
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        if remove:
            target.pop(parts[-1], None)
        else:
            target[parts[-1]] = value

        forged = dict(event)
        forged["_payload_override"] = payload
        with pytest.raises(sqlite3.IntegrityError, match="staging event failed"):
            registry._connection.execute(
                """INSERT INTO edition_staging_events
                   (event_id, state_revision, channel, operation, manifest_id, edition_id,
                    edition_revision, payload_sha256, export_id, export_sha256,
                    previous_current_export_id, lkg_export_id, operator_principal_id,
                    occurred_at, reason, export_package_json, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"], event["state_revision"], event["channel"],
                    event["operation"], event["manifest_id"], event["edition_id"],
                    event["edition_revision"], event["payload_sha256"], event["export_id"],
                    event["export_sha256"], event["previous_current_export_id"],
                    event["lkg_export_id"], event["operator_principal_id"],
                    event["occurred_at"], event["reason"],
                    json.dumps(package, sort_keys=True, separators=(",", ":")),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_staging_events"
        ).fetchone()[0] == 0
        assert registry._connection.execute(
            "SELECT state_revision FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "value", "remove"),
    [
        ("export_id", 7, False),
        ("manifest_id", None, True),
        ("payload_sha256", ["sha256:" + "a" * 64], False),
        ("target", None, True),
        ("publication_authorized", "false", False),
    ],
)
def test_staging_export_package_direct_sql_rejects_missing_keys_and_wrong_types(
    field, value, remove,
):
    with SQLiteRegistry() as registry:
        event, package = _valid_staging_sql_fixture(registry)
        if remove:
            package.pop(field, None)
        else:
            package[field] = value
        with pytest.raises(sqlite3.IntegrityError, match="staging event failed"):
            registry._connection.execute(
                """INSERT INTO edition_staging_events
                   (event_id, state_revision, channel, operation, manifest_id, edition_id,
                    edition_revision, payload_sha256, export_id, export_sha256,
                    previous_current_export_id, lkg_export_id, operator_principal_id,
                    occurred_at, reason, export_package_json, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"], event["state_revision"], event["channel"],
                    event["operation"], event["manifest_id"], event["edition_id"],
                    event["edition_revision"], event["payload_sha256"], event["export_id"],
                    event["export_sha256"], event["previous_current_export_id"],
                    event["lkg_export_id"], event["operator_principal_id"],
                    event["occurred_at"], event["reason"],
                    json.dumps(package, sort_keys=True, separators=(",", ":")),
                    json.dumps(event, sort_keys=True, separators=(",", ":")),
                ),
            )
        assert registry._connection.execute(
            "SELECT COUNT(*) FROM edition_staging_events"
        ).fetchone()[0] == 0
        assert registry._connection.execute(
            "SELECT state_revision FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone()[0] == 0
