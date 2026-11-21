from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sngso_engine.benchmark_access import AuthenticatedBenchmarkAccess, AuthenticatedIdentity
from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry
from sngso_engine import registry as registry_module
from sngso_engine import edition_artifacts


def identity(*, principal_id: str = "approver:one", authenticated_at=None) -> AuthenticatedIdentity:
    now = datetime.now(timezone.utc)
    return AuthenticatedIdentity(
        principal_id=principal_id,
        issuer="https://identity.example",
        audience="sarangsho-editorial",
        authenticated_at=authenticated_at or now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=10),
        session_id="opaque-session-value",
        subject="oidc-subject-123",
        authentication_methods=("pwd", "otp"),
        authentication_context="urn:example:mfa",
        provider_key_id="key-2026-01",
        provider_adapter="production-oidc-adapter",
    )


def prepare_candidate(registry: SQLiteRegistry, monkeypatch, *, approver_roles=None) -> str:
    manifest_id = "edition-manifest:daily-2026-11-21"
    edition_id = "edition:daily-2026-11-21"
    payload_hash = "sha256:" + "a" * 64
    manifest = {
        "manifest_id": manifest_id,
        "edition_id": edition_id,
        "edition_revision": 1,
        "payload_sha256": payload_hash,
    }
    registry._connection.execute(
        """INSERT INTO edition_artifacts
        (manifest_id, edition_id, edition_revision, payload_sha256,
         payload_json, manifest_json, verification_json, created_at, actor, reason)
        VALUES (?, ?, 1, ?, '{}', ?, '{}', ?, 'builder', 'test fixture')""",
        (
            manifest_id, edition_id, payload_hash, json.dumps(manifest),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    registry.register_benchmark_principal(
        "approver:one", display_name="Independent approver",
        roles=approver_roles or ["edition_approver"], provisioned_by="bootstrap",
        principal_kind="human", reason="test approver",
    )
    registry.get_edition_artifact = lambda requested: {
        "manifest_id": manifest_id,
        "edition_id": edition_id,
        "edition_revision": 1,
        "payload_sha256": payload_hash,
        "payload": {"edition_id": edition_id},
        "manifest": manifest,
    } if requested == manifest_id else None
    registry.get_learning_object = lambda object_id: SimpleNamespace(
        to_payload=lambda: {"language_review": {"reviewer_id": "reviewer:independent"}}
    )
    monkeypatch.setattr(
        edition_artifacts,
        "verify_registry_backed_edition_artifact",
        lambda *args, **kwargs: SimpleNamespace(
            artifact=SimpleNamespace(payload_sha256=payload_hash),
            learning_object_ids=("learning:bn", "learning:en"),
        ),
    )
    return manifest_id


def test_edition_decision_requires_authenticated_human_approver_and_is_append_only(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        result = registry.record_edition_decision(
            manifest_id, identity=identity(), decision="approve",
            reason="Independent review of the exact staging candidate",
        )
        assert result["decision"] == "approve"
        assert result["publication_authorized"] is False
        assert result["scope"] == "staging_candidate_review"
        assert result["identity_attestation"]["session_fingerprint"].startswith("sha256:")
        assert "opaque-session-value" not in json.dumps(result)
        assert registry.get_edition_decision(manifest_id) == result
        assert registry.list_edition_decisions("edition:daily-2026-11-21") == [result]
        with pytest.raises(RegistryIntegrityError, match="already has a final edition decision"):
            registry.record_edition_decision(
                manifest_id, identity=identity(), decision="reject", reason="Conflicting second decision"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE edition_decisions SET decision = 'reject' WHERE manifest_id = ?",
                (manifest_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM edition_decisions WHERE manifest_id = ?", (manifest_id,)
            )


def test_edition_decision_rejects_nonhuman_or_missing_approver_role(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        registry.revoke_benchmark_principal("approver:one", actor="security", reason="test")
        with pytest.raises(RegistryIntegrityError, match="active human edition approver"):
            registry.record_edition_decision(
                manifest_id, identity=identity(), decision="approve", reason="not authorized"
            )


def test_edition_decision_rejects_stale_authentication_and_reviewer_self_approval(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        stale = identity(authenticated_at=datetime.now(timezone.utc) - timedelta(minutes=16))
        with pytest.raises(RegistryIntegrityError, match="within 15 minutes"):
            registry.record_edition_decision(
                manifest_id, identity=stale, decision="approve", reason="stale session"
            )
        reviewer_identity = identity(principal_id="reviewer:independent")
        registry.register_benchmark_principal(
            "reviewer:independent", display_name="Reviewer",
            roles=["edition_approver"], provisioned_by="bootstrap",
            principal_kind="human", reason="test reviewer principal",
        )
        with pytest.raises(RegistryIntegrityError, match="independent"):
            registry.record_edition_decision(
                manifest_id, identity=reviewer_identity, decision="approve", reason="self approval"
            )


def test_schema_27_migrates_to_28_with_immutable_decision_ledger(tmp_path, monkeypatch) -> None:
    database = tmp_path / "edition-decisions-v27.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 27)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 27
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 28)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 28


def test_authenticated_facade_binds_edition_decision_to_provider_identity() -> None:
    with SQLiteRegistry() as registry:
        registry.register_benchmark_principal(
            "approver:one", display_name="Independent approver",
            roles=["edition_approver"], provisioned_by="bootstrap",
            principal_kind="human", reason="test approver",
        )
        now = datetime.now(timezone.utc)
        supplied_identity = identity()
        class Provider:
            def authenticate(self, request_context):
                assert request_context == {"session": "server-context"}
                return supplied_identity

        captured = {}
        registry.record_edition_decision = lambda manifest_id, **kwargs: captured.update(
            manifest_id=manifest_id, **kwargs
        ) or {"decision": kwargs["decision"]}
        access = AuthenticatedBenchmarkAccess(
            registry, Provider(),
            expected_issuer="https://identity.example",
            expected_audience="sarangsho-editorial",
            subject_to_principal=lambda issuer, subject: (
                "approver:one" if (issuer, subject) == (
                    "https://identity.example", "oidc-subject-123"
                ) else None
            ),
            clock=lambda: now,
        )
        result = access.decide_edition(
            {"session": "server-context"}, "edition-manifest:daily-2026-11-21",
            decision="approve", reason="Independent staging review",
        )
        assert result == {"decision": "approve"}
        assert captured["identity"] is supplied_identity
        assert captured["manifest_id"] == "edition-manifest:daily-2026-11-21"

        mismatched = AuthenticatedBenchmarkAccess(
            registry, Provider(),
            expected_issuer="https://identity.example",
            expected_audience="sarangsho-editorial",
            subject_to_principal=lambda issuer, subject: None,
            clock=lambda: now,
        )
        with pytest.raises(RegistryIntegrityError, match="not bound"):
            mismatched.decide_edition(
                {"session": "server-context"}, "edition-manifest:daily-2026-11-21",
                decision="approve", reason="Identity substitution attempt",
            )


def test_sqlite_insert_guard_binds_decision_to_exact_stored_artifact_and_role(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        decided_at = datetime.now(timezone.utc).isoformat()
        forged = {
            "schema_version": "1.0.0",
            "decision_id": "edition-decision:forged",
            "manifest_id": manifest_id,
            "edition_id": "edition:forged",
            "edition_revision": 1,
            "payload_sha256": "sha256:" + "b" * 64,
            "decision": "approve",
            "scope": "staging_candidate_review",
            "principal_id": "approver:one",
            "role": "edition_approver",
            "decided_at": decided_at,
            "reason": "forged direct insert",
            "identity_attestation": {},
            "publication_authorized": False,
        }
        with pytest.raises(sqlite3.IntegrityError, match="exact artifact and active human approver"):
            registry._connection.execute(
                """INSERT INTO edition_decisions
                (decision_id, manifest_id, edition_id, edition_revision,
                 payload_sha256, decision, principal_id, decided_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    forged["decision_id"], manifest_id, forged["edition_id"],
                    forged["edition_revision"], forged["payload_sha256"],
                    forged["decision"], forged["principal_id"], decided_at,
                    json.dumps(forged),
                ),
            )


def edition_payload(status: str = "approved") -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "edition:controlled-release-test",
        "edition_key": "2026-11-21",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "content_hash": "sha256:" + "a" * 64,
        "pipeline_run_id": "pipeline-run:release-test",
        "story_ids": [],
        "language_variants": [{"language": "en", "status": "draft", "text": "Staging candidate"}],
    }


def test_generic_edition_insert_cannot_publish_and_sqlite_keeps_edition_immutable() -> None:
    with SQLiteRegistry() as registry:
        with pytest.raises(RegistryIntegrityError, match="controlled release workflow"):
            registry.add_domain_record("edition", edition_payload("published"))

        approved = registry.add_domain_record("edition", edition_payload("approved"))
        assert approved.status == "approved"
        forged = edition_payload("published")
        with pytest.raises(sqlite3.IntegrityError, match="controlled release workflow"):
            registry._connection.execute(
                """INSERT INTO domain_records
                (record_type, id, revision, created_at, updated_at, payload_json)
                VALUES ('edition', ?, 1, ?, ?, ?)""",
                (
                    forged["id"], forged["created_at"], forged["created_at"],
                    json.dumps(forged),
                ),
            )
        changed = edition_payload("published")
        with pytest.raises(sqlite3.IntegrityError, match="edition snapshots are immutable"):
            registry._connection.execute(
                "UPDATE domain_records SET payload_json = ? WHERE record_type = 'edition' AND id = ?",
                (json.dumps(changed), approved.id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="edition snapshots are immutable"):
            registry._connection.execute(
                "DELETE FROM domain_records WHERE record_type = 'edition' AND id = ?",
                (approved.id,),
            )


def test_schema_28_migrates_to_29_with_edition_publication_guards(tmp_path, monkeypatch) -> None:
    database = tmp_path / "edition-guard-v28.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 28)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 28
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 29)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 29
        triggers = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "edition_domain_records_no_published_insert" in triggers
        assert "edition_domain_records_no_update" in triggers
        assert "edition_domain_records_no_delete" in triggers


def test_two_person_release_authorization_requires_prior_approval_and_is_append_only(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        registry.register_benchmark_principal(
            "release:two", display_name="Release authorizer",
            roles=["edition_release_authorizer"], provisioned_by="bootstrap",
            principal_kind="human", reason="test release authorizer",
        )
        approval = registry.record_edition_decision(
            manifest_id, identity=identity(), decision="approve",
            reason="Independent editorial approval",
        )
        release_identity = identity(principal_id="release:two")
        authorization = registry.authorize_edition_release(
            manifest_id, identity=release_identity,
            reason="Separate release-gate authorization after fresh verification",
        )
        assert authorization["approval_decision_id"] == approval["decision_id"]
        assert authorization["approver_principal_id"] == "approver:one"
        assert authorization["authorizer_principal_id"] == "release:two"
        assert authorization["release_authorized"] is True
        assert authorization["publication_authorized"] is False
        assert authorization["scope"] == "controlled_release_gate"
        assert registry.get_edition_release_authorization(manifest_id) == authorization
        assert "opaque-session-value" not in json.dumps(authorization)
        with pytest.raises(RegistryIntegrityError, match="already has a release authorization"):
            registry.authorize_edition_release(
                manifest_id, identity=release_identity, reason="Duplicate authorization"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE edition_release_authorizations SET authorizer_principal_id = 'forged' WHERE manifest_id = ?",
                (manifest_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM edition_release_authorizations WHERE manifest_id = ?",
                (manifest_id,),
            )


def test_two_person_release_authorization_rejects_missing_rejected_or_same_person_approval(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        registry.register_benchmark_principal(
            "release:two", display_name="Release authorizer",
            roles=["edition_release_authorizer"], provisioned_by="bootstrap",
            principal_kind="human", reason="test release authorizer",
        )
        release_identity = identity(principal_id="release:two")
        with pytest.raises(RegistryIntegrityError, match="requires an approved staging decision"):
            registry.authorize_edition_release(
                manifest_id, identity=release_identity, reason="No prior decision"
            )
        registry.record_edition_decision(
            manifest_id, identity=identity(), decision="reject", reason="Candidate rejected"
        )
        with pytest.raises(RegistryIntegrityError, match="requires an approved staging decision"):
            registry.authorize_edition_release(
                manifest_id, identity=release_identity, reason="Rejected candidate"
            )

    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(
            registry, monkeypatch,
            approver_roles=["edition_approver", "edition_release_authorizer"],
        )
        registry.record_edition_decision(
            manifest_id, identity=identity(), decision="approve", reason="Approved candidate"
        )
        with pytest.raises(RegistryIntegrityError, match="different from the staging approver"):
            registry.authorize_edition_release(
                manifest_id, identity=identity(), reason="Attempted self authorization"
            )


def test_two_person_release_authorization_requires_active_human_authorizer_and_fresh_auth(monkeypatch) -> None:
    with SQLiteRegistry() as registry:
        manifest_id = prepare_candidate(registry, monkeypatch)
        registry.register_benchmark_principal(
            "release:two", display_name="Release authorizer",
            roles=["edition_release_authorizer"], provisioned_by="bootstrap",
            principal_kind="human", reason="test release authorizer",
        )
        registry.record_edition_decision(
            manifest_id, identity=identity(), decision="approve", reason="Approved candidate"
        )
        stale = identity(
            principal_id="release:two",
            authenticated_at=datetime.now(timezone.utc) - timedelta(minutes=16),
        )
        with pytest.raises(RegistryIntegrityError, match="within 15 minutes"):
            registry.authorize_edition_release(
                manifest_id, identity=stale, reason="Stale authentication"
            )
        registry.revoke_benchmark_principal(
            "release:two", actor="security", reason="test revocation"
        )
        with pytest.raises(RegistryIntegrityError, match="active human edition_release_authorizer"):
            registry.authorize_edition_release(
                manifest_id, identity=identity(principal_id="release:two"),
                reason="Revoked authorizer",
            )


def test_schema_29_migrates_to_30_with_release_authorization_ledger(tmp_path, monkeypatch) -> None:
    database = tmp_path / "edition-release-v29.sqlite"
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 29)
    with SQLiteRegistry(database) as old:
        assert old.schema_version() == 29
    monkeypatch.setattr(registry_module, "_SCHEMA_VERSION", 30)
    with SQLiteRegistry(database) as migrated:
        assert migrated.schema_version() == 30
        triggers = {
            row["name"] for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "edition_release_authorizations_insert_guard" in triggers
        assert "edition_release_authorizations_no_update" in triggers
        assert "edition_release_authorizations_no_delete" in triggers


def test_authenticated_facade_exposes_two_person_release_authorization() -> None:
    with SQLiteRegistry() as registry:
        registry.register_benchmark_principal(
            "release:two", display_name="Release authorizer",
            roles=["edition_release_authorizer"], provisioned_by="bootstrap",
            principal_kind="human", reason="test release authorizer",
        )
        now = datetime.now(timezone.utc)
        supplied_identity = identity(principal_id="release:two")
        class Provider:
            def authenticate(self, request_context):
                assert request_context == {"session": "release-context"}
                return supplied_identity

        captured = {}
        registry.authorize_edition_release = lambda manifest_id, **kwargs: captured.update(
            manifest_id=manifest_id, **kwargs
        ) or {"release_authorized": True}
        access = AuthenticatedBenchmarkAccess(
            registry, Provider(),
            expected_issuer="https://identity.example",
            expected_audience="sarangsho-editorial",
            subject_to_principal=lambda issuer, subject: (
                "release:two" if (issuer, subject) == (
                    "https://identity.example", "oidc-subject-123"
                ) else None
            ),
            clock=lambda: now,
        )
        result = access.authorize_edition_release(
            {"session": "release-context"},
            "edition-manifest:daily-2026-11-21",
            reason="Second-person authorization",
        )
        assert result == {"release_authorized": True}
        assert captured["identity"] is supplied_identity
        assert captured["manifest_id"] == "edition-manifest:daily-2026-11-21"
