from __future__ import annotations

import sqlite3

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
        assert registry.schema_version() == 13
        registry.add_source(source_record())
    with SQLiteRegistry(database) as reopened:
        assert reopened.schema_version() == 13
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
        "benchmark_fingerprint": "sha256:" + "c" * 64,
        "language": "en",
        "status": "approved",
        "reviewer": {
            "reviewer_id": "editor:en-01",
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
        }],
        "corrections": [],
        "adjudication_note": "Checked against the registered article and source evidence.",
    }


def _populate_benchmark_review_sources(registry: SQLiteRegistry) -> None:
    registry.add_source(source_record())
    registry.add_permission_evidence(evidence_record())
    registry.add_domain_record("article", article_record())


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
        with pytest.raises(sqlite3.IntegrityError, match="fingerprint binding are immutable"):
            registry.revise_benchmark_review(
                payload["review_id"], changed, expected_revision=1,
                actor="editor:second", reason="attempt to rebind old review",
            )
