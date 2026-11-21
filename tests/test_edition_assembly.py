from __future__ import annotations

import json
from copy import deepcopy

import pytest

from sngso_engine.edition_assembly import (
    EditionAssemblyError,
    assemble_bilingual_edition_artifact,
)


class Record:
    def __init__(self, payload, revision=1):
        self._payload = deepcopy(payload)
        self.revision = revision

    def to_payload(self):
        return deepcopy(self._payload)


class AssemblyRegistry:
    def __init__(self):
        self.edition = Record({
            "id": "edition:assembly",
            "edition_key": "2026-11-21",
            "status": "in_review",
            "pipeline_run_id": "pipeline-run:assembly",
            "story_ids": ["story:one"],
        })
        self.story = {
            "story_id": "story:one",
            "story_revision": 3,
            "fingerprint": "sha256:" + "a" * 64,
            "story": {"id": "story:one", "canonical_title": "A developing story", "status": "developing"},
            "articles": [{
                "id": "article:one",
                "revision": 2,
                "record": {
                    "id": "article:one", "source_id": "source:one",
                    "canonical_uri": "https://publisher.example/news/story",
                    "title": "Original publisher headline",
                    "source_language": "en", "record_status": "active",
                    "published_at": "2026-11-21T04:00:00Z",
                },
            }],
        }
        self.objects = {
            "learning:bn": Record({
                "id": "learning:bn", "story_id": "story:one",
                "kind": "headline_summary", "language": "bn", "status": "approved",
                "variant_group_id": "variant:one", "claim_ids": ["claim:one"],
                "content": {"text": "বাংলা শিরোনাম", "segments": [
                    {"text": "বাংলা শিরোনাম", "claim_ids": ["claim:one"], "role": "fact"}
                ]},
            }, 2),
            "learning:en": Record({
                "id": "learning:en", "story_id": "story:one",
                "kind": "headline_summary", "language": "en", "status": "approved",
                "variant_group_id": "variant:one", "claim_ids": ["claim:one"],
                "content": {"text": "English headline", "segments": [
                    {"text": "English headline", "claim_ids": ["claim:one"], "role": "fact"}
                ]},
            }, 4),
        }

    def get_domain_record(self, kind, identifier):
        return self.edition if (kind, identifier) == ("edition", "edition:assembly") else None

    def domain_record_history(self, kind, identifier):
        if (kind, identifier) == ("edition", "edition:assembly"):
            return [{"revision": 1, "record": self.edition.to_payload()}]
        return []

    def story_editorial_snapshot(self, story_id):
        return deepcopy(self.story)

    def get_learning_object(self, object_id):
        return self.objects.get(object_id)

    def learning_object_history(self, object_id):
        record = self.objects.get(object_id)
        return [{"revision": record.revision, "record": record.to_payload()}] if record else []

    def learning_object_quality_report(self, object_id):
        return {"meets_registry_quality_gates": True, "blockers": []}

    def get_source(self, source_id):
        if source_id != "source:one":
            return None
        return Record({"id": source_id, "publisher_name": "Example Publisher", "status": "active"})


def _assemble(monkeypatch, registry=None, selected=None, permissions=None):
    import sngso_engine.edition_assembly as assembly
    monkeypatch.setattr(
        assembly, "verify_independent_bilingual_variants",
        lambda registry, bn, en: {"publication_authorized": False},
    )
    monkeypatch.setattr(
        assembly, "verify_pipeline_run_for_artifact",
        lambda registry, manifest, digest: object(),
    )
    return assembly.assemble_bilingual_edition_artifact(
        registry or AssemblyRegistry(),
        "edition:assembly",
        selected or {"story:one": ["learning:bn", "learning:en"]},
        permissions or [{
            "source_id": "source:one", "endpoint_id": "endpoint:one",
            "grant_id": "grant:one", "operation": "republication",
        }],
        manifest_id="edition-manifest:assembly",
        created_at="2026-11-21T06:00:00Z",
    )


def test_assembly_preserves_exact_approved_bilingual_content_and_provenance(monkeypatch):
    registry = AssemblyRegistry()
    assembled = _assemble(monkeypatch, registry)
    payload = json.loads(assembled.artifact.payload_bytes)
    manifest = json.loads(assembled.artifact.manifest_bytes)
    story = payload["stories"][0]
    assert story["story_id"] == "story:one"
    assert story["story_revision"] == 3
    assert story["sources"][0]["article_revision"] == 2
    objects = {item["language"]: item for item in story["learning_objects"]}
    assert objects["bn"]["content"]["text"] == "বাংলা শিরোনাম"
    assert objects["en"]["content"]["text"] == "English headline"
    assert objects["bn"]["claim_ids"] == objects["en"]["claim_ids"] == ["claim:one"]
    assert manifest["story_bindings"][0]["story_snapshot_sha256"] == registry.story["fingerprint"]
    assert assembled.story_ids == ("story:one",)
    assert assembled.learning_object_ids == ("learning:bn", "learning:en")
    assert assembled.payload_sha256 == manifest["payload_sha256"]


def test_assembly_rejects_incomplete_language_pair(monkeypatch):
    with pytest.raises(EditionAssemblyError, match="incomplete BN/EN pair"):
        _assemble(monkeypatch, selected={"story:one": ["learning:bn"]})


def test_assembly_rejects_unapproved_learning_object(monkeypatch):
    registry = AssemblyRegistry()
    payload = registry.objects["learning:en"].to_payload()
    payload["status"] = "draft"
    registry.objects["learning:en"] = Record(payload, 4)
    with pytest.raises(EditionAssemblyError, match="not approved"):
        _assemble(monkeypatch, registry)


def test_assembly_rejects_stale_learning_object_history(monkeypatch):
    registry = AssemblyRegistry()
    registry.objects["learning:bn"].revision = 5
    original_history = registry.learning_object_history
    registry.learning_object_history = lambda object_id: (
        [{"revision": 4, "record": registry.objects[object_id].to_payload()}]
        if object_id == "learning:bn" else original_history(object_id)
    )
    with pytest.raises(EditionAssemblyError, match="revision history mismatch"):
        _assemble(monkeypatch, registry)


def test_assembly_requires_explicit_selection_for_every_story(monkeypatch):
    registry = AssemblyRegistry()
    registry.edition = Record({
        "id": "edition:assembly", "edition_key": "2026-11-21",
        "status": "in_review", "pipeline_run_id": "pipeline-run:assembly",
        "story_ids": ["story:one", "story:two"],
    })
    with pytest.raises(EditionAssemblyError, match="cover every edition story"):
        _assemble(monkeypatch, registry)


def test_assembly_rejects_missing_source_article(monkeypatch):
    registry = AssemblyRegistry()
    registry.story["articles"] = []
    with pytest.raises(EditionAssemblyError, match="no active source articles"):
        _assemble(monkeypatch, registry)


def test_assembly_rejects_timezone_naive_manifest_time(monkeypatch):
    import sngso_engine.edition_assembly as assembly
    with pytest.raises(EditionAssemblyError, match="include a timezone"):
        assembly.assemble_bilingual_edition_artifact(
            AssemblyRegistry(), "edition:assembly",
            {"story:one": ["learning:bn", "learning:en"]},
            [{"source_id": "source:one", "endpoint_id": "endpoint:one",
              "grant_id": "grant:one", "operation": "republication"}],
            manifest_id="edition-manifest:assembly",
            created_at="2026-11-21T06:00:00",
        )


# Integrated real-SQLite lifecycle coverage. Unlike the focused staging tests in
# tests/storage/test_edition_decisions.py, this path uses actual registry records
# and the unmocked whole-edition verifier for persistence, approval, export,
# promotion, and rollback.
def test_real_registry_bilingual_edition_lifecycle_through_rollback(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    import json

    from sngso_engine.benchmark_access import AuthenticatedIdentity
    from sngso_engine.edition_artifacts import verify_registry_backed_edition_artifact
    from sngso_engine.edition_export import build_verified_staging_export
    from sngso_engine.edition_staging import (
        get_edition_staging_state, list_edition_staging_events,
        promote_edition_to_staging, rollback_edition_staging,
    )
    from sngso_engine.registry import SQLiteRegistry

    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    host = "publisher.example.test"
    source_id, endpoint_id = "source:publisher", "endpoint:publisher-news"
    grant_id, permission_evidence_id = "permission:publisher", "permission-evidence:publisher"
    article_id, story_id, claim_id = "article:notice", "story:notice", "claim:notice"

    def auth(principal_id):
        return AuthenticatedIdentity(
            principal_id=principal_id, issuer="https://identity.example.test/",
            audience="sarangsho-editorial", authenticated_at=now-timedelta(minutes=1),
            expires_at=now+timedelta(minutes=10), session_id="opaque:"+principal_id,
            subject="oidc:"+principal_id, authentication_methods=("pwd","mfa"),
            authentication_context="urn:example:mfa", provider_key_id="test-key",
            provider_adapter="test-oidc-adapter/1",
        )

    with SQLiteRegistry() as registry:
        for principal_id, roles, languages in (
            ("reviewer:bn", ["benchmark_reviewer"], ["bn"]),
            ("reviewer:en", ["benchmark_reviewer"], ["en"]),
            ("approver:one", ["edition_approver"], []),
            ("authorizer:one", ["edition_release_authorizer"], []),
            ("operator:one", ["edition_staging_operator"], []),
        ):
            registry.register_benchmark_principal(
                principal_id, display_name=principal_id, roles=roles, languages=languages,
                provisioned_by="test-bootstrap", principal_kind="human",
                reason="integration test principal",
            )

        registry.add_source({
            "schema_version":"1.0.0","id":source_id,"publisher_name":"Example Publisher",
            "publisher_type":"newspaper","canonical_homepage":f"https://{host}/",
            "domains":[host],"country_code":"BD","languages":["bn","en"],"status":"active",
            "created_at":stamp,"updated_at":stamp,
        })
        registry.add_endpoint({
            "schema_version":"1.0.0","id":endpoint_id,"source_id":source_id,
            "endpoint_uri":f"https://{host}/news/","endpoint_type":"rss","status":"active",
            "supported_languages":["bn","en"],"created_at":stamp,"updated_at":stamp,
        })
        registry.add_domain_record("article", {
            "schema_version":"1.0.0","id":article_id,"source_id":source_id,
            "canonical_uri":f"https://{host}/news/notice","title":"Authority issues notice",
            "source_language":"en","discovered_at":stamp,"published_at":stamp,"record_status":"active",
        })
        registry.add_domain_record("story", {
            "schema_version":"1.0.0","id":story_id,"canonical_title":"Public notice issued",
            "status":"developing","created_at":stamp,"updated_at":stamp,"article_ids":[article_id],
        })
        registry.add_domain_record("claim", {
            "schema_version":"1.0.0","id":claim_id,"story_id":story_id,
            "text":"The authority issued a public notice.","language":"en","assessment":"unassessed",
            "provenance":[{"source_id":source_id,"article_id":article_id,"observed_at":stamp,
                "method":"human_review","method_version":"1.0",
                "locator":{"uri":f"https://{host}/news/notice"}}],"created_at":stamp,
        })
        registry.add_domain_record("evidence", {
            "schema_version":"1.0.0","id":"evidence:notice","claim_id":claim_id,
            "source_id":source_id,"article_id":article_id,"relationship":"supports",
            "assessment_method":"human","locator":{"uri":f"https://{host}/news/notice"},
            "assessed_at":stamp,"assessed_by":"evidence-reviewer",
        })
        claim=registry.get_domain_record("claim",claim_id).to_payload()
        claim.update({"assessment":"supported","evidence_ids":["evidence:notice"],
                      "reviewed_at":stamp,"review_note":"Linked source evidence supports the claim."})
        registry.revise_domain_record("claim",claim_id,claim,expected_revision=1,
            actor="evidence-reviewer",reason="Link supporting evidence.")

        registry.add_permission_evidence({
            "schema_version":"1.0.0","id":permission_evidence_id,"source_id":source_id,
            "evidence_type":"written_grant","evidence_uri":f"https://{host}/permissions/republication",
            "captured_at":stamp,"content_fingerprint":"sha256:"+"a"*64,
            "scope_description":"Synthetic integration fixture scoped to the registered endpoint.",
            "review_status":"verified","reviewed_at":stamp,"reviewed_by":"rights-reviewer",
        })
        operations={}
        for operation in ("discovery","metadata","excerpt_storage","full_text_storage",
                          "translation","image_storage","republication"):
            allowed=operation=="republication"
            operations[operation]={"allowed":allowed,"status":"approved" if allowed else "denied",
                "conditions":[],**({"evidence_ids":[permission_evidence_id]} if allowed else {})}
        registry.add_permission_grant({
            "schema_version":"1.0.0","id":grant_id,"source_id":source_id,
            "endpoint_ids":[endpoint_id],"status":"approved","operations":operations,
            "evidence_ids":[permission_evidence_id],"basis":"Synthetic integration fixture.",
            "effective_from":(now-timedelta(days=1)).isoformat(),
            "effective_until":(now+timedelta(days=30)).isoformat(),
            "created_at":stamp,"reviewed_at":stamp,"reviewed_by":"rights-reviewer",
        })

        selected=[]
        for language, reviewer_id, text_value in (
            ("bn","reviewer:bn","কর্তৃপক্ষ একটি সরকারি বিজ্ঞপ্তি জারি করেছে।"),
            ("en","reviewer:en","The authority issued a public notice."),
        ):
            draft=registry.add_learning_object({
                "schema_version":"1.0.0","id":f"learning:notice-{language}",
                "story_id":story_id,"kind":"headline_summary","language":language,"status":"draft",
                "variant_group_id":"variant:notice",
                "content":{"text":text_value,"segments":[{"text":text_value,"claim_ids":[claim_id],"role":"fact"}]},
                "claim_ids":[claim_id],"created_at":stamp,
            },actor="editor:"+language,reason="Create language variant.")
            dimensions=(
                {"grammar_and_spelling":"pass","naturalness_and_register":"pass",
                 "terminology_consistency":"pass","factual_fidelity":"pass","attribution":"pass",
                 "omissions":"pass","translation_fidelity":"pass"} if language=="bn" else
                {"grammar_and_style":"pass","clarity_and_readability":"pass",
                 "terminology_consistency":"pass","factual_fidelity":"pass","attribution":"pass",
                 "omissions":"pass","translation_fidelity":"pass"}
            )
            reviewed_at=(now+timedelta(seconds=1)).isoformat()
            reviewed=draft.to_payload()
            reviewed.update({
                "status":"approved","reviewed_at":reviewed_at,"reviewed_by":reviewer_id,
                "review_note":"Independent language review completed.",
                "language_review":{
                    "language":language,"rubric_version":"bn-editorial-v1" if language=="bn" else "en-editorial-v1",
                    "reviewer_id":reviewer_id,"reviewed_at":reviewed_at,
                    "note":"All language rubric dimensions passed.","dimensions":dimensions,
                    "identity_attestation":{
                        "principal_id":reviewer_id,"issuer":"https://identity.example.test/",
                        "subject":"oidc:"+reviewer_id,"provider_key_id":"test-key",
                        "provider_adapter":"test-oidc-adapter/1",
                        "authenticated_at":(now-timedelta(minutes=1)).isoformat(),
                        "attested_at":reviewed_at,"authentication_methods":["pwd","mfa"],
                        "authentication_context":"urn:example:mfa",
                    },
                },
            })
            registry.revise_learning_object(draft.id,reviewed,expected_revision=1,
                actor=reviewer_id,reason="Approve independently reviewed variant.")
            selected.append(draft.id)

        source_permissions=[{"source_id":source_id,"endpoint_id":endpoint_id,
                             "grant_id":grant_id,"operation":"republication"}]

        def create_candidate(suffix):
            import sngso_engine.edition_assembly as assembly
            edition_id="edition:"+suffix
            pipeline_id="pipeline-run:"+suffix
            manifest_id="edition-manifest:"+suffix
            edition_key="2026-11-22_"+suffix
            created_at=(now+timedelta(minutes=1)).isoformat()
            stub={"schema_version":"1.0.0","id":edition_id,"edition_key":edition_key,
                  "created_at":created_at,"status":"approved","content_hash":"sha256:"+"0"*64,
                  "pipeline_run_id":pipeline_id,"story_ids":[story_id],
                  "language_variants":[{"language":"bn","status":"approved"},
                                       {"language":"en","status":"approved"}]}
            class PreviewRegistry:
                def __getattr__(self,name): return getattr(registry,name)
                def get_domain_record(self,kind,identifier):
                    if kind=="edition" and identifier==edition_id:
                        return SimpleNamespace(to_payload=lambda:dict(stub))
                    return registry.get_domain_record(kind,identifier)
                def domain_record_history(self,kind,identifier):
                    if kind=="edition" and identifier==edition_id:
                        return [{"revision":1,"record":dict(stub)}]
                    return registry.domain_record_history(kind,identifier)
            with monkeypatch.context() as preview_patch:
                preview_patch.setattr(assembly,"verify_pipeline_run_for_artifact",
                    lambda *args,**kwargs: SimpleNamespace(pipeline_run_id=pipeline_id))
                preview=assemble_bilingual_edition_artifact(
                    PreviewRegistry(),edition_id,{story_id:selected},source_permissions,
                    manifest_id=manifest_id,created_at=created_at,
                )
            run={"schema_version":"1.0.0","id":pipeline_id,"status":"running",
                 "started_at":now.isoformat(),"software_version":"integration-test/1.0",
                 "config_hash":"sha256:"+"b"*64,"input_hashes":["sha256:"+"c"*64],
                 "initiated_by":"test-scheduler"}
            registry.add_pipeline_run(run,actor="test-scheduler",reason="Start deterministic test run.")
            completed=dict(run,status="succeeded",
                finished_at=(now+timedelta(seconds=30)).isoformat(),
                output_hashes=[preview.payload_sha256])
            registry.transition_pipeline_run(pipeline_id,completed,expected_revision=1,
                actor="test-worker",reason="Record exact artifact output.")
            registry.add_domain_record("edition",{**stub,"content_hash":preview.payload_sha256})
            assembled=assemble_bilingual_edition_artifact(
                registry,edition_id,{story_id:selected},source_permissions,
                manifest_id=manifest_id,created_at=created_at,
            )
            verified=verify_registry_backed_edition_artifact(
                registry,json.loads(assembled.artifact.payload_bytes),
                json.loads(assembled.artifact.manifest_bytes),now=now+timedelta(minutes=2),
            )
            stored=registry.store_verified_edition_artifact(
                verified,actor="test-builder",reason="Persist verified bilingual artifact.")
            assert stored["integrity_verified"] is True
            return manifest_id

        first=create_candidate("morning")
        second=create_candidate("evening")
        for manifest_id in (first,second):
            registry.record_edition_decision(manifest_id,identity=auth("approver:one"),
                decision="approve",reason="Approve exact bilingual staging artifact.")
            registry.authorize_edition_release(manifest_id,identity=auth("authorizer:one"),
                reason="Independently authorize exact staging artifact.")
            assert build_verified_staging_export(registry,manifest_id).publication_authorized is False

        p1=promote_edition_to_staging(registry,first,identity=auth("operator:one"),
            reason="Promote first verified candidate.",expected_state_revision=0,
            expected_current_export_id=None)
        first_export=p1["export"].export_id
        p2=promote_edition_to_staging(registry,second,identity=auth("operator:one"),
            reason="Promote second verified candidate.",expected_state_revision=1,
            expected_current_export_id=first_export)
        second_export=p2["export"].export_id
        rollback=rollback_edition_staging(registry,first,target_export_id=first_export,
            identity=auth("operator:one"),reason="Restore previous verified staging candidate.",
            expected_state_revision=2,expected_current_export_id=second_export)

        assert rollback["state"]["state_revision"]==3
        assert rollback["state"]["current_export_id"]==first_export
        assert rollback["state"]["lkg_export_id"]==second_export
        assert [event["operation"] for event in list_edition_staging_events(registry)]==[
            "promote","promote","rollback"
        ]
        assert get_edition_staging_state(registry)["current_export_id"]==first_export
        assert registry.verify_audit_chain()


def test_registry_nested_transaction_uses_savepoint_and_rolls_back_inner_scope():
    from sngso_engine.registry import SQLiteRegistry

    with SQLiteRegistry() as registry:
        with registry._transaction() as connection:
            connection.execute("CREATE TABLE nested_transaction_probe (value TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO nested_transaction_probe VALUES ('outer-before')")
            with pytest.raises(RuntimeError, match="abort inner scope"):
                with registry._transaction("DEFERRED") as nested:
                    nested.execute("INSERT INTO nested_transaction_probe VALUES ('inner')")
                    raise RuntimeError("abort inner scope")
            connection.execute("INSERT INTO nested_transaction_probe VALUES ('outer-after')")

        values = [
            row["value"] for row in registry._connection.execute(
                "SELECT value FROM nested_transaction_probe ORDER BY value"
            )
        ]
        assert values == ["outer-after", "outer-before"]
        assert registry._connection.in_transaction is False


def test_outer_registry_rollback_reverts_successful_nested_savepoint():
    from sngso_engine.registry import SQLiteRegistry

    with SQLiteRegistry() as registry:
        with pytest.raises(RuntimeError, match="abort outer scope"):
            with registry._transaction() as connection:
                connection.execute("CREATE TABLE outer_rollback_probe (value TEXT)")
                with registry._transaction() as nested:
                    nested.execute("INSERT INTO outer_rollback_probe VALUES ('nested-write')")
                raise RuntimeError("abort outer scope")

        table = registry._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='outer_rollback_probe'"
        ).fetchone()
        assert table is None
        assert registry._connection.in_transaction is False
