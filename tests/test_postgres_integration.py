from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.postgres_audit import append_audit_event
from sngso_engine.postgres_migration_runner import apply_postgres_migrations
from sngso_engine.postgres_transaction import run_atomic


@pytest.fixture
def postgres_schema():
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is not configured")

    schema = "sngso_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    try:
        with connect() as connection:
            assert apply_postgres_migrations(connection) == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def _append(connect, index):
    with connect() as connection:
        return run_atomic(
            connection,
            lambda cursor: append_audit_event(
                cursor,
                event_id=f"parallel-{index}",
                event_type="integration.test",
                entity_type="test",
                entity_id=str(index),
                occurred_at="2026-11-23T00:00:00Z",
                payload={"index": index},
            ),
        )


def test_postgres_migrations_execute_and_are_idempotent(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        assert apply_postgres_migrations(connection) == ()
        rows = connection.execute(
            "SELECT version, name FROM postgres_schema_migrations ORDER BY version"
        ).fetchall()
        assert rows == [(1, "0001_audit_chain.sql"), (2, "0002_audit_head_guards.sql"), (3, "0003_source_permission_domain_registry.sql"), (4, "0004_collection_ingestion.sql"), (5, "0005_story_timeline.sql"), (6, "0006_perspectives_claim_relations.sql"), (7, "0007_learning_glossary_revisions.sql"), (8, "0008_benchmark_review_core.sql"), (9, "0009_benchmark_assignments_identity_scores.sql"), (10, "0010_benchmark_study_evidence.sql"), (11, "0011_learning_specialized_evidence.sql"), (12, "0012_pipeline_runs.sql"), (13, "0013_pipeline_stage_checkpoints.sql"), (14, "0014_pipeline_worker_leases.sql"), (15, "0015_edition_artifacts.sql"), (16, "0016_edition_decisions.sql"), (17, "0017_edition_snapshot_guards.sql"), (18, "0018_edition_release_authorizations.sql"), (19, "0019_edition_staging_state.sql"), (20, "0020_final_revision_and_assignment_guards.sql"), (21, "0021_domain_record_revision_guards.sql"), (22, "0022_learning_evidence_locking.sql")]



def test_postgres_source_permission_registry_preserves_composite_source_boundaries(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES ('source-a', 'Publisher A', 'active', '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s), ('source-b', 'Publisher B', 'active', "
            "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "source-a"}), json.dumps({"id": "source-b"})),
        )
        connection.execute(
            "INSERT INTO source_endpoints "
            "(id, source_id, endpoint_uri, status, created_at, updated_at, payload_json) "
            "VALUES ('endpoint-a', 'source-a', 'https://a.example/feed', 'active', "
            "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "endpoint-a"}),),
        )
        connection.execute(
            "INSERT INTO permission_evidence "
            "(id, source_id, review_status, content_fingerprint, captured_at, payload_json) "
            "VALUES ('evidence-a', 'source-a', 'approved', 'sha256:" + "a" * 64 + "', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "evidence-a"}),),
        )
        connection.execute(
            "INSERT INTO permission_grants "
            "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
            "VALUES ('grant-a', 'source-a', 'active', '2026-11-23T00:00:00Z', "
            "'2027-11-23T00:00:00Z', 1, %s)",
            (json.dumps({"id": "grant-a", "revision": 1}),),
        )
        connection.execute(
            "INSERT INTO grant_endpoints (grant_id, source_id, endpoint_id) "
            "VALUES ('grant-a', 'source-a', 'endpoint-a')"
        )
        connection.execute(
            "INSERT INTO permission_grants "
            "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
            "VALUES ('grant-b', 'source-b', 'active', '2026-11-23T00:00:00Z', "
            "'2027-11-23T00:00:00Z', 1, %s)",
            (json.dumps({"id": "grant-b", "revision": 1}),),
        )
        connection.execute(
            "INSERT INTO grant_evidence (grant_id, source_id, evidence_id) "
            "VALUES ('grant-a', 'source-a', 'evidence-a')"
        )
        connection.execute(
            "INSERT INTO grant_operation_evidence "
            "(grant_id, source_id, operation, evidence_id) "
            "VALUES ('grant-a', 'source-a', 'discovery', 'evidence-a')"
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO grant_endpoints (grant_id, source_id, endpoint_id) "
                    "VALUES ('grant-b', 'source-b', 'endpoint-a')"
                )
        assert connection.execute(
            "SELECT count(*) FROM grant_endpoints WHERE grant_id = 'grant-a'"
        ).fetchone()[0] == 1


def test_postgres_registry_revision_snapshots_are_append_only(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES ('source-revision', 'Publisher', 'active', '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "source-revision"}),),
        )
        connection.execute(
            "INSERT INTO permission_grants "
            "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
            "VALUES ('grant-revision', 'source-revision', 'active', '2026-11-23T00:00:00Z', "
            "'2027-11-23T00:00:00Z', 1, %s)",
            (json.dumps({"id": "grant-revision", "revision": 1}),),
        )
        connection.execute(
            "INSERT INTO permission_grant_revisions "
            "(grant_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('grant-revision', 1, '2026-11-23T00:00:00Z', 'reviewer', "
            "'initial grant', %s)",
            (json.dumps({"id": "grant-revision", "revision": 1}),),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('story', 'story-revision', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s, 'reviewer', 'initial record', "
            "'2026-11-23T00:00:00Z')",
            (json.dumps({"id": "story-revision", "revision": 1}),),
        )
        connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('story', 'story-revision', 1, '2026-11-23T00:00:00Z', "
            "'reviewer', 'initial record', %s)",
            (json.dumps({"id": "story-revision", "revision": 1}),),
        )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE permission_grant_revisions SET reason = 'rewritten' "
                "WHERE grant_id = 'grant-revision' AND revision = 1"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM domain_record_revisions "
                "WHERE record_type = 'story' AND record_id = 'story-revision' AND revision = 1"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM permission_grant_revisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM domain_record_revisions"
        ).fetchone()[0] == 1



def test_postgres_collection_ingestion_provenance_and_current_pointer(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        for source_id in ("source-ingest-a", "source-ingest-b"):
            connection.execute(
                "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
                "VALUES (%s, 'Publisher', 'active', '2026-11-23T00:00:00Z', "
                "'2026-11-23T00:00:00Z', %s)",
                (source_id, json.dumps({"id": source_id})),
            )
        connection.execute(
            "INSERT INTO source_endpoints "
            "(id, source_id, endpoint_uri, status, created_at, updated_at, payload_json) "
            "VALUES ('endpoint-ingest', 'source-ingest-a', 'https://ingest.example/feed', "
            "'active', '2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "endpoint-ingest"}),),
        )
        connection.execute(
            "INSERT INTO permission_grants "
            "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
            "VALUES ('grant-ingest', 'source-ingest-a', 'active', '2026-11-23T00:00:00Z', "
            "'2027-11-23T00:00:00Z', 1, %s)",
            (json.dumps({"id": "grant-ingest", "revision": 1}),),
        )
        connection.execute(
            "INSERT INTO collection_observations "
            "(observation_id, source_id, endpoint_id, grant_id, grant_revision, operation, "
            "connector_name, connector_version, observed_at, locator, metadata_json, "
            "permission_reason, http_method) "
            "VALUES ('observation-ingest', 'source-ingest-a', 'endpoint-ingest', "
            "'grant-ingest', 1, 'discovery', 'test-connector', '1.0', "
            "'2026-11-23T00:00:00Z', 'https://ingest.example/article', %s, "
            "'approved discovery scope', 'GET')",
            (json.dumps({"title": "Observed article"}),),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO collection_observations "
                    "(observation_id, source_id, endpoint_id, grant_id, grant_revision, "
                    "operation, connector_name, connector_version, observed_at, locator, "
                    "metadata_json, permission_reason, http_method) "
                    "VALUES ('observation-post', 'source-ingest-a', 'endpoint-ingest', "
                    "'grant-ingest', 1, 'discovery', 'test-connector', '1.0', "
                    "'2026-11-23T00:00:00Z', 'https://ingest.example/post', %s, "
                    "'not allowed', 'POST')",
                    (json.dumps({}),),
                )
        for article_id, source_id, uri in (
            ("article-ingest-a", "source-ingest-a", "https://ingest.example/article"),
            ("article-ingest-b", "source-ingest-b", "https://other.example/article"),
        ):
            connection.execute(
                "INSERT INTO domain_records "
                "(record_type, id, revision, created_at, updated_at, payload_json) "
                "VALUES ('article', %s, 1, '2026-11-23T00:00:00Z', "
                "'2026-11-23T00:00:00Z', %s)",
                (article_id, json.dumps({
                    "id": article_id, "source_id": source_id, "canonical_uri": uri
                })),
            )
        connection.execute(
            "INSERT INTO ingestion_decisions "
            "(decision_id, observation_id, decision_revision, source_id, article_id, "
            "outcome, fingerprint, canonical_uri, reason, decided_at) "
            "VALUES ('decision-ingest', 'observation-ingest', 1, 'source-ingest-a', "
            "'article-ingest-a', 'created', %s, 'https://ingest.example/article', "
            "'new source article', '2026-11-23T00:00:00Z')",
            ("sha256:" + "b" * 64,),
        )
        connection.execute(
            "INSERT INTO ingestion_current (observation_id, decision_id) "
            "VALUES ('observation-ingest', 'decision-ingest')"
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="article/source/URI mismatch"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO ingestion_decisions "
                    "(decision_id, observation_id, decision_revision, source_id, article_id, "
                    "outcome, canonical_uri, reason, decided_at) "
                    "VALUES ('decision-wrong-source', 'observation-ingest', 2, "
                    "'source-ingest-a', 'article-ingest-b', 'reused', "
                    "'https://other.example/article', 'wrong source', "
                    "'2026-11-23T00:00:01Z')"
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE ingestion_decisions SET reason = 'rewritten' "
                "WHERE decision_id = 'decision-ingest'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM collection_observations "
                "WHERE observation_id = 'observation-ingest'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM ingestion_current WHERE observation_id = 'observation-ingest'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT decision_id FROM ingestion_current "
            "WHERE observation_id = 'observation-ingest'"
        ).fetchone() == ("decision-ingest",)



def test_postgres_story_decisions_and_timeline_event_invariants(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('story', 'story-timeline', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "story-timeline", "status": "active", "article_ids": []}),),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('claim', 'claim-timeline', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "claim-timeline", "story_id": "story-timeline"}),),
        )
        connection.execute(
            "INSERT INTO story_decisions "
            "(id, operation, canonical_story_id, story_ids_json, article_ids_json, "
            "decided_by, rationale, decided_at, payload_json) "
            "VALUES ('story-decision-1', 'merge', 'story-timeline', %s, %s, "
            "'editor', 'duplicate story records', '2026-11-23T00:00:00Z', %s)",
            (json.dumps(["story-timeline"]), json.dumps([]),
             json.dumps({"decision": "merge"})),
        )
        connection.execute(
            "INSERT INTO story_decision_applications "
            "(application_id, decision_id, applied_at, actor, rationale, "
            "decision_fingerprint, impact_fingerprint, result_json) "
            "VALUES ('story-application-1', 'story-decision-1', "
            "'2026-11-23T00:00:01Z', 'editor', 'reviewed impact', %s, %s, %s)",
            ("sha256:" + "c" * 64, "sha256:" + "d" * 64,
             json.dumps({"applied": True})),
        )
        connection.execute(
            "INSERT INTO timeline_events "
            "(id, story_id, occurred_sort_key, precision, created_at, payload_json) "
            "VALUES ('timeline-event-1', 'story-timeline', '2026-11-22', 'date', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({
                "claim_ids": ["claim-timeline"],
                "review_status": "approved",
                "reviewed_at": "2026-11-23T00:00:00Z",
                "reviewed_by": "reviewer",
                "review_note": "Evidence checked"
            }),),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="claims must belong to its story"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO timeline_events "
                    "(id, story_id, precision, created_at, payload_json) "
                    "VALUES ('timeline-event-bad', 'story-timeline', 'date', "
                    "'2026-11-23T00:00:00Z', %s)",
                    (json.dumps({"claim_ids": ["claim-not-in-story"]}),),
                )
        with pytest.raises(psycopg.errors.CheckViolation, match="requires reviewer"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO timeline_events "
                    "(id, story_id, precision, created_at, payload_json) "
                    "VALUES ('timeline-event-unreviewed', 'story-timeline', 'date', "
                    "'2026-11-23T00:00:00Z', %s)",
                    (json.dumps({
                        "claim_ids": ["claim-timeline"],
                        "review_status": "approved",
                        "reviewed_by": "reviewer"
                    }),),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE story_decisions SET rationale = 'rewritten' "
                "WHERE id = 'story-decision-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM story_decision_applications "
                "WHERE application_id = 'story-application-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE timeline_events SET occurred_sort_key = '2026-11-24' "
                "WHERE id = 'timeline-event-1'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM timeline_events"
        ).fetchone()[0] == 1



def test_postgres_perspectives_and_claim_relations_preserve_story_provenance(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES ('source-perspective', 'Publisher', 'active', '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "source-perspective"}),),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('story', 'story-perspective', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({
                "id": "story-perspective", "status": "active",
                "article_ids": ["article-perspective"]
            }),),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('article', 'article-perspective', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({
                "id": "article-perspective", "source_id": "source-perspective"
            }),),
        )
        for claim_id in ("claim-perspective-a", "claim-perspective-b"):
            connection.execute(
                "INSERT INTO domain_records "
                "(record_type, id, revision, created_at, updated_at, payload_json) "
                "VALUES ('claim', %s, 1, '2026-11-23T00:00:00Z', "
                "'2026-11-23T00:00:00Z', %s)",
                (claim_id, json.dumps({
                    "id": claim_id, "story_id": "story-perspective"
                })),
            )
        connection.execute(
            "INSERT INTO perspectives "
            "(id, story_id, source_id, article_id, created_at, payload_json) "
            "VALUES ('perspective-1', 'story-perspective', 'source-perspective', "
            "'article-perspective', '2026-11-23T00:00:00Z', %s)",
            (json.dumps({
                "kind": "publisher_framing",
                "claim_ids": ["claim-perspective-a"],
                "review_status": "approved",
                "reviewed_at": "2026-11-23T00:00:00Z",
                "reviewed_by": "reviewer",
                "review_note": "Publisher attribution verified"
            }),),
        )
        connection.execute(
            "INSERT INTO claim_relations "
            "(id, story_id, claim_id, related_claim_id, relationship, created_at, payload_json) "
            "VALUES ('claim-relation-1', 'story-perspective', 'claim-perspective-a', "
            "'claim-perspective-b', 'qualifies', '2026-11-23T00:00:00Z', %s)",
            (json.dumps({"review_status": "unreviewed"}),),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="publisher framing requires"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO perspectives "
                    "(id, story_id, source_id, created_at, payload_json) "
                    "VALUES ('perspective-invalid', 'story-perspective', "
                    "'source-perspective', '2026-11-23T00:00:00Z', %s)",
                    (json.dumps({
                        "kind": "publisher_framing",
                        "claim_ids": []
                    }),),
                )
        with pytest.raises(psycopg.errors.CheckViolation, match="two different claims"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO claim_relations "
                    "(id, story_id, claim_id, related_claim_id, relationship, "
                    "created_at, payload_json) "
                    "VALUES ('claim-relation-invalid', 'story-perspective', "
                    "'claim-perspective-a', 'claim-perspective-a', 'contradicts', "
                    "'2026-11-23T00:00:00Z', %s)",
                    (json.dumps({}),),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE perspectives SET created_at = '2026-11-24T00:00:00Z' "
                "WHERE id = 'perspective-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM claim_relations WHERE id = 'claim-relation-1'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM perspectives"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM claim_relations"
        ).fetchone()[0] == 1



def test_postgres_learning_and_glossary_revision_snapshots_are_atomic_and_append_only(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('story', 'story-learning', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "story-learning", "status": "active"}),),
        )
        connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES ('learning-1', 'story-learning', 'brief_summary', 'en', 'draft', 1, "
            "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', 'editor', "
            "'initial draft', %s)",
            (json.dumps({"claim_ids": [], "content": {"text": "Initial"}}),),
        )
        connection.execute(
            "UPDATE learning_objects SET status = 'approved', revision = 2, "
            "updated_at = '2026-11-23T00:01:00Z', last_actor = 'reviewer', "
            "last_reason = 'approved after review', "
            "payload_json = %s WHERE id = 'learning-1'",
            (json.dumps({
                "claim_ids": [], "content": {"text": "Reviewed"},
                "reviewed_at": "2026-11-23T00:01:00Z",
                "reviewed_by": "reviewer", "review_note": "Checked"
            }),),
        )
        connection.execute(
            "INSERT INTO glossary_entries "
            "(id, lemma, lemma_key, term_language, part_of_speech, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES ('glossary-1', 'resilience', 'resilience', 'en', 'noun', 'draft', 1, "
            "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', 'editor', "
            "'initial term', %s)",
            (json.dumps({"lemma": "resilience", "senses": []}),),
        )
        connection.execute(
            "UPDATE glossary_entries SET status = 'approved', revision = 2, "
            "updated_at = '2026-11-23T00:02:00Z', last_actor = 'reviewer', "
            "last_reason = 'approved term', payload_json = %s "
            "WHERE id = 'glossary-1'",
            (json.dumps({
                "lemma": "resilience", "senses": [],
                "reviewed_at": "2026-11-23T00:02:00Z",
                "reviewed_by": "reviewer", "review_note": "Definition verified"
            }),),
        )
        assert connection.execute(
            "SELECT revision, count(*) OVER () FROM learning_object_revisions "
            "WHERE learning_object_id = 'learning-1' ORDER BY revision"
        ).fetchall() == [(1, 2), (2, 2)]
        assert connection.execute(
            "SELECT revision FROM glossary_entry_revisions "
            "WHERE glossary_entry_id = 'glossary-1' ORDER BY revision"
        ).fetchall() == [(1,), (2,)]
        with pytest.raises(psycopg.errors.CheckViolation, match="revision must increment by one"):
            with connection.transaction():
                connection.execute(
                    "UPDATE learning_objects SET revision = 4, "
                    "updated_at = '2026-11-23T00:03:00Z', last_actor = 'editor', "
                    "last_reason = 'skip revision' WHERE id = 'learning-1'"
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE learning_object_revisions SET reason = 'rewritten' "
                "WHERE learning_object_id = 'learning-1' AND revision = 1"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM glossary_entry_revisions "
                "WHERE glossary_entry_id = 'glossary-1' AND revision = 1"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM learning_objects WHERE id = 'learning-1'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM learning_object_revisions"
        ).fetchone()[0] == 2



def test_postgres_benchmark_artifact_review_and_adjudication_integrity(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, "
            "content_json, created_at, actor, reason) "
            "VALUES ('benchmark-artifact-1', 'en', 'canonical-json-v1', %s, "
            "'{}', '2026-11-23T00:00:00Z', 'builder', 'frozen benchmark')",
            ("sha256:" + "e" * 64,),
        )
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, "
            "created_by, reason, languages_json, principal_kind) "
            "VALUES ('benchmark-reviewer', 'Reviewer', %s, 1, "
            "'2026-11-23T00:00:00Z', 'admin', 'review role', %s, 'human')",
            (json.dumps(["reviewer"]), json.dumps(["en", "bn"])),
        )
        connection.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, "
            "revision, created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES ('benchmark-review-1', 'benchmark-artifact-1', %s, 'en', "
            "'draft', 1, '2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', "
            "'reviewer', 'initial review', %s)",
            ("sha256:" + "e" * 64, json.dumps({"findings": []})),
        )
        connection.execute(
            "UPDATE benchmark_reviews SET status = 'approved', revision = 2, "
            "updated_at = '2026-11-23T00:01:00Z', last_actor = 'reviewer', "
            "last_reason = 'review completed', payload_json = %s "
            "WHERE review_id = 'benchmark-review-1'",
            (json.dumps({"findings": ["verified"]}),),
        )
        assert connection.execute(
            "SELECT revision FROM benchmark_review_revisions "
            "WHERE review_id = 'benchmark-review-1' ORDER BY revision"
        ).fetchall() == [(1,), (2,)]
        connection.execute(
            "INSERT INTO benchmark_adjudications "
            "(adjudication_id, benchmark_id, benchmark_fingerprint, adjudicator_id, "
            "resolved_at, payload_json, created_at, actor, reason) "
            "VALUES ('adjudication-1', 'benchmark-artifact-1', %s, "
            "'benchmark-reviewer', '2026-11-23T00:02:00Z', %s, "
            "'2026-11-23T00:02:00Z', 'reviewer', 'final adjudication')",
            ("sha256:" + "e" * 64, json.dumps({"decision": "accepted"})),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO benchmark_adjudications "
                    "(adjudication_id, benchmark_id, benchmark_fingerprint, adjudicator_id, "
                    "resolved_at, payload_json, created_at, actor, reason) "
                    "VALUES ('adjudication-2', 'benchmark-artifact-1', %s, "
                    "'benchmark-reviewer', '2026-11-23T00:03:00Z', %s, "
                    "'2026-11-23T00:03:00Z', 'reviewer', 'duplicate adjudication')",
                    ("sha256:" + "e" * 64, json.dumps({"decision": "rejected"})),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE benchmark_artifacts SET reason = 'rewritten' "
                "WHERE artifact_id = 'benchmark-artifact-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE benchmark_principals SET principal_kind = 'synthetic' "
                "WHERE principal_id = 'benchmark-reviewer'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM benchmark_review_revisions "
                "WHERE review_id = 'benchmark-review-1' AND revision = 1"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM benchmark_adjudications"
        ).fetchone()[0] == 1



def test_postgres_benchmark_assignments_identity_attestations_and_scores_are_durable(postgres_schema):
    connect = postgres_schema
    fingerprint = "sha256:" + "f" * 64
    timestamp = "2026-11-23T00:00:00Z"
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES ('artifact-assign', 'en', "
            "'canonical-json-v1', %s, '{}', %s, 'builder', 'frozen artifact')",
            (fingerprint, timestamp),
        )
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, "
            "reason, languages_json, principal_kind) "
            "VALUES ('reviewer-assign', 'Reviewer', %s, 1, %s, 'admin', "
            "'reviewer principal', %s, 'human'), "
            "('curator-assign', 'Curator', %s, 1, %s, 'admin', "
            "'curator principal', %s, 'human')",
            (
                json.dumps(["benchmark_reviewer"]), timestamp, json.dumps(["en"]),
                json.dumps(["benchmark_curator"]), timestamp, json.dumps(["en"]),
            ),
        )
        connection.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES ('review-identity', 'artifact-assign', %s, 'en', 'draft', 1, "
            "%s, %s, 'reviewer-assign', 'initial review', %s)",
            (fingerprint, timestamp, timestamp, json.dumps({"findings": []})),
        )
        assignment_payload = {
            "assignment_id": "assignment-1",
            "artifact_id": "artifact-assign",
            "artifact_fingerprint": fingerprint,
            "language": "en",
            "reviewer_id": "reviewer-assign",
            "assigned_by": "curator-assign",
            "status": "assigned",
        }
        connection.execute(
            "INSERT INTO benchmark_review_assignments "
            "(assignment_id, artifact_id, artifact_fingerprint, language, reviewer_id, "
            "assigned_by, assigned_at, due_at, status, revision, payload_json, updated_at, "
            "last_actor, last_reason) "
            "VALUES ('assignment-1', 'artifact-assign', %s, 'en', 'reviewer-assign', "
            "'curator-assign', %s, '2026-11-24T00:00:00Z', 'assigned', 1, %s, %s, "
            "'curator-assign', 'initial assignment')",
            (fingerprint, timestamp, json.dumps(assignment_payload), timestamp),
        )
        assert connection.execute(
            "SELECT revision, actor, reason FROM benchmark_review_assignment_revisions "
            "WHERE assignment_id = 'assignment-1'"
        ).fetchall() == [(1, "curator-assign", "initial assignment")]

        accepted_payload = {**assignment_payload, "status": "accepted", "accepted_at": "2026-11-23T00:00:30Z", "last_transition": {"actor": "reviewer-assign", "reason": "accepted assignment"}}
        connection.execute(
            "UPDATE benchmark_review_assignments SET status = 'accepted', revision = 2, "
            "payload_json = %s, updated_at = '2026-11-23T00:01:00Z', "
            "last_actor = 'reviewer-assign', last_reason = 'accepted assignment' "
            "WHERE assignment_id = 'assignment-1'",
            (json.dumps(accepted_payload),),
        )
        assert connection.execute(
            "SELECT revision FROM benchmark_review_assignment_revisions "
            "WHERE assignment_id = 'assignment-1' ORDER BY revision"
        ).fetchall() == [(1,), (2,)]

        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                duplicate_payload = {**assignment_payload, "assignment_id": "assignment-duplicate"}
                connection.execute(
                    "INSERT INTO benchmark_review_assignments "
                    "(assignment_id, artifact_id, artifact_fingerprint, language, reviewer_id, "
                    "assigned_by, assigned_at, due_at, status, revision, payload_json, updated_at, "
                    "last_actor, last_reason) VALUES ('assignment-duplicate', 'artifact-assign', "
                    "%s, 'en', 'reviewer-assign', 'curator-assign', %s, "
                    "'2026-11-25T00:00:00Z', 'assigned', 1, %s, %s, "
                    "'curator-assign', 'duplicate active assignment')",
                    (fingerprint, timestamp, json.dumps(duplicate_payload), timestamp),
                )

        connection.execute(
            "INSERT INTO benchmark_review_identity_attestations "
            "(attestation_id, review_id, review_revision, principal_id, issuer, audience, "
            "subject, authenticated_at, expires_at, authentication_methods_json, "
            "authentication_context, provider_key_id, provider_adapter, session_fingerprint, "
            "attested_at, payload_json, actor, reason) "
            "VALUES ('identity-1', 'review-identity', 1, 'reviewer-assign', "
            "'https://identity.example', 'benchmark-review', 'reviewer-subject', %s, "
            "'2026-11-23T01:00:00Z', %s, 'mfa', 'key-1', 'oidc-test', %s, %s, %s, "
            "'reviewer-assign', 'identity verified')",
            (
                timestamp, json.dumps(["webauthn"]), "sha256:" + "a" * 64,
                timestamp, json.dumps({"verified": True}),
            ),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO benchmark_review_identity_attestations "
                    "(attestation_id, review_id, review_revision, principal_id, issuer, audience, "
                    "subject, authenticated_at, expires_at, authentication_methods_json, "
                    "authentication_context, provider_key_id, provider_adapter, session_fingerprint, "
                    "attested_at, payload_json, actor, reason) "
                    "VALUES ('identity-duplicate', 'review-identity', 1, 'reviewer-assign', "
                    "'https://identity.example', 'benchmark-review', 'reviewer-subject', %s, "
                    "'2026-11-23T01:00:00Z', %s, 'mfa', 'key-1', 'oidc-test', %s, %s, %s, "
                    "'reviewer-assign', 'duplicate identity proof')",
                    (
                        timestamp, json.dumps(["webauthn"]), "sha256:" + "a" * 64,
                        timestamp, json.dumps({"verified": True}),
                    ),
                )

        score_payload = {
            "benchmark_id": "artifact-assign",
            "benchmark_fingerprint": fingerprint,
            "item_id": "item-1",
            "task_type": "short_answer",
            "language": "en",
            "evaluator_id": "reviewer-assign",
            "evaluated_at": timestamp,
        }
        connection.execute(
            "INSERT INTO benchmark_scores "
            "(score_id, assignment_id, artifact_id, artifact_fingerprint, item_id, task_type, "
            "language, evaluator_id, evaluated_at, weighted_score, eligible_for_comparison, "
            "score_fingerprint, payload_json, issuer, subject, provider_key_id, provider_adapter, "
            "authenticated_at, created_at, reason) "
            "VALUES ('score-1', 'assignment-1', 'artifact-assign', %s, 'item-1', "
            "'short_answer', 'en', 'reviewer-assign', %s, '0.85', 1, %s, %s, "
            "'https://identity.example', 'reviewer-subject', 'key-1', 'oidc-test', "
            "%s, %s, 'accepted reviewer score')",
            (
                fingerprint, timestamp, "sha256:" + "b" * 64, json.dumps(score_payload),
                timestamp, timestamp,
            ),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO benchmark_scores "
                    "(score_id, assignment_id, artifact_id, artifact_fingerprint, item_id, task_type, "
                    "language, evaluator_id, evaluated_at, weighted_score, eligible_for_comparison, "
                    "score_fingerprint, payload_json, issuer, subject, provider_key_id, provider_adapter, "
                    "authenticated_at, created_at, reason) "
                    "VALUES ('score-duplicate', 'assignment-1', 'artifact-assign', %s, 'item-1', "
                    "'short_answer', 'en', 'reviewer-assign', %s, '0.90', 1, %s, %s, "
                    "'https://identity.example', 'reviewer-subject', 'key-1', 'oidc-test', "
                    "%s, %s, 'duplicate item score')",
                    (
                        fingerprint, timestamp, "sha256:" + "c" * 64, json.dumps(score_payload),
                        timestamp, timestamp,
                    ),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE benchmark_review_identity_attestations SET reason = 'rewritten' "
                "WHERE attestation_id = 'identity-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM benchmark_scores WHERE score_id = 'score-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM benchmark_review_assignment_revisions "
                "WHERE assignment_id = 'assignment-1' AND revision = 1"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM benchmark_scores WHERE assignment_id = 'assignment-1'"
        ).fetchone() == (1,)


def test_postgres_benchmark_study_protocol_execution_and_acceptance_evidence(postgres_schema):
    connect = postgres_schema
    timestamp = "2026-11-23T00:00:00Z"
    benchmark_fingerprint = "sha256:" + "d" * 64
    protocol_fingerprint_v1 = "sha256:" + "1" * 64
    protocol_fingerprint_v2 = "sha256:" + "2" * 64
    execution_fingerprint = "sha256:" + "3" * 64
    acceptance_fingerprint = "sha256:" + "4" * 64
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES ('study-benchmark', 'en', "
            "'canonical-json-v1', %s, '{}', %s, 'builder', 'study artifact')",
            (benchmark_fingerprint, timestamp),
        )
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, "
            "reason, languages_json, principal_kind) VALUES "
            "('methodologist-1', 'Methodologist', %s, 1, %s, 'admin', 'methodologist', %s, 'human'), "
            "('acceptance-reviewer-1', 'Acceptance reviewer', %s, 1, %s, 'admin', 'independent reviewer', %s, 'human')",
            (
                json.dumps(["benchmark_methodologist"]), timestamp, json.dumps(["en"]),
                json.dumps(["independent_acceptance_reviewer"]), timestamp, json.dumps(["en"]),
            ),
        )

        protocol_v1 = {
            "study_id": "study-1", "study_version": 1,
            "benchmark_id": "study-benchmark",
            "benchmark_fingerprint": benchmark_fingerprint,
            "rubric_version": "rubric-1", "status": "protocol_only",
        }
        connection.execute(
            "INSERT INTO benchmark_study_protocols "
            "(protocol_record_id, study_id, study_version, benchmark_id, benchmark_fingerprint, "
            "rubric_version, protocol_fingerprint, status, payload_json, created_at, actor, reason) "
            "VALUES ('protocol-1-v1', 'study-1', 1, 'study-benchmark', %s, 'rubric-1', "
            "%s, 'protocol_only', %s, %s, 'curator', 'initial protocol')",
            (benchmark_fingerprint, protocol_fingerprint_v1, json.dumps(protocol_v1), timestamp),
        )
        protocol_v2 = {**protocol_v1, "study_version": 2}
        connection.execute(
            "INSERT INTO benchmark_study_protocols "
            "(protocol_record_id, study_id, study_version, benchmark_id, benchmark_fingerprint, "
            "rubric_version, protocol_fingerprint, status, payload_json, created_at, actor, reason) "
            "VALUES ('protocol-1-v2', 'study-1', 2, 'study-benchmark', %s, 'rubric-1', "
            "%s, 'protocol_only', %s, '2026-11-23T00:01:00Z', 'curator', 'protocol revision')",
            (benchmark_fingerprint, protocol_fingerprint_v2, json.dumps(protocol_v2)),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="versions must be sequential"):
            with connection.transaction():
                invalid_protocol = {**protocol_v1, "study_version": 4}
                connection.execute(
                    "INSERT INTO benchmark_study_protocols "
                    "(protocol_record_id, study_id, study_version, benchmark_id, benchmark_fingerprint, "
                    "rubric_version, protocol_fingerprint, status, payload_json, created_at, actor, reason) "
                    "VALUES ('protocol-1-v4', 'study-1', 4, 'study-benchmark', %s, 'rubric-1', "
                    "'sha256:" + "5" * 64 + "', 'protocol_only', %s, %s, 'curator', 'skip version')",
                    (benchmark_fingerprint, json.dumps(invalid_protocol), timestamp),
                )

        execution_payload = {
            "execution_id": "execution-1",
            "study_id": "study-1",
            "study_version": 2,
            "protocol_fingerprint": protocol_fingerprint_v2,
            "benchmark_id": "study-benchmark",
            "benchmark_fingerprint": benchmark_fingerprint,
            "rubric_version": "rubric-1",
            "analysis": {"methodologist_id": "methodologist-1"},
        }
        connection.execute(
            "INSERT INTO benchmark_study_executions "
            "(execution_id, protocol_record_id, study_id, study_version, protocol_fingerprint, "
            "benchmark_id, benchmark_fingerprint, rubric_version, execution_fingerprint, status, "
            "methodologist_id, issuer, subject, provider_key_id, provider_adapter, authenticated_at, "
            "payload_json, created_at, actor, reason) "
            "VALUES ('execution-1', 'protocol-1-v2', 'study-1', 2, %s, 'study-benchmark', "
            "%s, 'rubric-1', %s, 'analysis_reviewed', 'methodologist-1', "
            "'https://identity.example', 'methodologist-subject', 'key-methodologist', 'oidc-test', "
            "%s, %s, %s, 'methodologist-1', 'analysis reviewed')",
            (
                protocol_fingerprint_v2, benchmark_fingerprint, execution_fingerprint,
                timestamp, json.dumps(execution_payload), timestamp,
            ),
        )

        acceptance_payload = {
            "review_id": "acceptance-1",
            "execution_id": "execution-1",
            "study_id": "study-1",
            "study_version": 2,
            "protocol_fingerprint": protocol_fingerprint_v2,
            "execution_fingerprint": execution_fingerprint,
            "benchmark_id": "study-benchmark",
            "benchmark_fingerprint": benchmark_fingerprint,
            "rubric_version": "rubric-1",
            "reviewer_id": "acceptance-reviewer-1",
            "reviewer_role": "independent_acceptance_reviewer",
            "decision": "accepted_for_limited_use",
        }
        connection.execute(
            "INSERT INTO benchmark_acceptance_reviews "
            "(review_id, execution_id, protocol_record_id, study_id, study_version, "
            "protocol_fingerprint, execution_fingerprint, acceptance_fingerprint, benchmark_id, "
            "benchmark_fingerprint, rubric_version, reviewer_id, reviewer_role, decision, issuer, "
            "subject, provider_key_id, provider_adapter, authenticated_at, payload_json, created_at, "
            "actor, reason) VALUES ('acceptance-1', 'execution-1', 'protocol-1-v2', 'study-1', 2, "
            "%s, %s, %s, 'study-benchmark', %s, 'rubric-1', 'acceptance-reviewer-1', "
            "'independent_acceptance_reviewer', 'accepted_for_limited_use', "
            "'https://identity.example', 'acceptance-subject', 'key-acceptance', 'oidc-test', "
            "%s, %s, %s, 'acceptance-reviewer-1', 'limited-use decision')",
            (
                protocol_fingerprint_v2, execution_fingerprint, acceptance_fingerprint,
                benchmark_fingerprint, timestamp, json.dumps(acceptance_payload), timestamp,
            ),
        )

        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                duplicate_acceptance = {**acceptance_payload, "review_id": "acceptance-duplicate"}
                connection.execute(
                    "INSERT INTO benchmark_acceptance_reviews "
                    "(review_id, execution_id, protocol_record_id, study_id, study_version, "
                    "protocol_fingerprint, execution_fingerprint, acceptance_fingerprint, benchmark_id, "
                    "benchmark_fingerprint, rubric_version, reviewer_id, reviewer_role, decision, issuer, "
                    "subject, provider_key_id, provider_adapter, authenticated_at, payload_json, created_at, "
                    "actor, reason) VALUES ('acceptance-duplicate', 'execution-1', 'protocol-1-v2', "
                    "'study-1', 2, %s, %s, 'sha256:" + "6" * 64 + "', 'study-benchmark', %s, "
                    "'rubric-1', 'acceptance-reviewer-1', 'independent_acceptance_reviewer', "
                    "'accepted_for_limited_use', 'https://identity.example', 'acceptance-subject', "
                    "'key-acceptance', 'oidc-test', %s, %s, %s, 'acceptance-reviewer-1', 'duplicate')",
                    (
                        protocol_fingerprint_v2, execution_fingerprint, benchmark_fingerprint,
                        timestamp, json.dumps(duplicate_acceptance), timestamp,
                    ),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE benchmark_study_protocols SET reason = 'rewritten' "
                "WHERE protocol_record_id = 'protocol-1-v1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM benchmark_study_executions WHERE execution_id = 'execution-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE benchmark_acceptance_reviews SET decision = 'rejected' "
                "WHERE review_id = 'acceptance-1'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT count(*) FROM benchmark_study_protocols WHERE study_id = 'study-1'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM benchmark_acceptance_reviews"
        ).fetchone() == (1,)

def test_postgres_audit_append_serializes_independent_connections(postgres_schema):
    connect = postgres_schema
    count = 16
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: _append(connect, index), range(count)))

    assert sorted(result["sequence"] for result in results) == list(range(1, count + 1))
    with connect() as connection:
        rows = connection.execute(
            "SELECT sequence, previous_hash, event_hash FROM audit_events ORDER BY sequence"
        ).fetchall()
        head = connection.execute(
            "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()

    assert len(rows) == count
    assert rows[0][1] is None
    for previous, current in zip(rows, rows[1:]):
        assert current[1] == previous[2]
    assert head == (count, rows[-1][2])

    with connect() as connection:
        events = connection.execute(
            "SELECT sequence, event_id, event_type, entity_type, entity_id, occurred_at, "
            "policy_version, payload_json, previous_hash, event_hash "
            "FROM audit_events ORDER BY sequence"
        ).fetchall()
    for event in events:
        (sequence, event_id, event_type, entity_type, entity_id, occurred_at,
         policy_version, payload_json, previous_hash, event_hash) = event
        expected = sha256_fingerprint({
            "event_id": event_id, "event_type": event_type,
            "entity_type": entity_type, "entity_id": entity_id,
            "occurred_at": occurred_at, "policy_version": policy_version,
            "payload": json.loads(payload_json), "previous_hash": previous_hash,
        })
        assert event_hash == expected, f"audit hash mismatch at sequence {sequence}"


def test_postgres_audit_event_and_head_roll_back_together(postgres_schema):
    connect = postgres_schema

    def fail_after_append(cursor):
        append_audit_event(
            cursor,
            event_id="must-roll-back",
            event_type="integration.test",
            entity_type="test",
            entity_id="rollback",
            occurred_at="2026-11-23T00:00:00Z",
            payload={"rollback": True},
        )
        raise RuntimeError("injected failure after audit insert and head update")

    with connect() as connection:
        with pytest.raises(RuntimeError, match="injected failure"):
            run_atomic(connection, fail_after_append)

    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
        assert connection.execute(
            "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone() == (0, None)



def test_postgres_default_runtime_role_cannot_mutate_audit_storage(postgres_schema):
    connect = postgres_schema
    role_name = "sngso_audit_reader_" + uuid.uuid4().hex[:12]
    schema_name = None
    try:
        with connect() as owner:
            schema_name = owner.execute("SELECT current_schema()").fetchone()[0]
            owner.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name))
            )
            owner.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            owner.execute(
                sql.SQL("GRANT SELECT ON audit_events TO {}").format(
                    sql.Identifier(role_name)
                )
            )

        with connect() as restricted:
            restricted.autocommit = True
            restricted.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(role_name))
            )
            assert restricted.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
            denied_statements = (
                "UPDATE audit_events SET event_type = 'tampered' WHERE false",
                "DELETE FROM audit_events WHERE false",
                "TRUNCATE audit_events",
                "UPDATE audit_chain_head SET sequence = 0 WHERE singleton_id = 1",
                "DELETE FROM audit_chain_head WHERE singleton_id = 1",
                "TRUNCATE audit_chain_head",
            )
            for statement in denied_statements:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    restricted.execute(statement)
    finally:
        if schema_name is not None:
            with psycopg.connect(
                os.environ["TEST_POSTGRES_DSN"], autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )



def test_postgres_runtime_writer_can_append_but_cannot_rewrite_audit_head(postgres_schema):
    connect = postgres_schema
    role_name = "sngso_audit_writer_" + uuid.uuid4().hex[:12]
    schema_name = None
    try:
        with connect() as owner:
            schema_name = owner.execute("SELECT current_schema()").fetchone()[0]
            owner.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name))
            )
            owner.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            owner.execute(
                sql.SQL("GRANT SELECT, INSERT ON audit_events TO {}").format(
                    sql.Identifier(role_name)
                )
            )
            owner.execute(
                sql.SQL("GRANT SELECT, UPDATE ON audit_chain_head TO {}").format(
                    sql.Identifier(role_name)
                )
            )

        with connect() as writer:
            writer.autocommit = True
            writer.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_name)))
            appended = run_atomic(
                writer,
                lambda cursor: append_audit_event(
                    cursor,
                    event_id="restricted-writer-append",
                    event_type="integration.test",
                    entity_type="test",
                    entity_id="restricted-writer",
                    occurred_at="2026-11-23T00:00:00Z",
                    payload={"writer": "restricted"},
                ),
            )
            assert appended["sequence"] == 1

        with connect() as restricted:
            restricted.autocommit = True
            restricted.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(role_name))
            )
            with pytest.raises(psycopg.errors.CheckViolation, match="advance exactly one"):
                restricted.execute(
                    "UPDATE audit_chain_head SET sequence = 0, event_hash = NULL "
                    "WHERE singleton_id = 1"
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute(
                    "DELETE FROM audit_chain_head WHERE singleton_id = 1"
                )
            with pytest.raises(psycopg.errors.CheckViolation, match="extend the current chain head"):
                restricted.execute(
                    "INSERT INTO audit_events "
                    "(sequence, event_id, event_type, entity_type, entity_id, occurred_at, "
                    "payload_json, previous_hash, event_hash) "
                    "VALUES (3, 'skipped-sequence', 'integration.test', 'test', 'skip', "
                    "'2026-11-23T00:00:01Z', '{}', %s, %s)",
                    (appended["event_hash"], "sha256:" + "0" * 64),
                )

        with connect() as owner:
            assert owner.execute(
                "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
            ).fetchone() == (1, appended["event_hash"])
    finally:
        if schema_name is not None:
            with psycopg.connect(
                os.environ["TEST_POSTGRES_DSN"], autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )


def test_postgres_duplicate_audit_event_id_rolls_back_without_advancing_chain(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        first = run_atomic(
            connection,
            lambda cursor: append_audit_event(
                cursor, event_id="stable-operation-id", event_type="integration.test",
                entity_type="test", entity_id="first",
                occurred_at="2026-11-23T00:00:00Z", payload={"attempt": 1},
            ),
        )

    with connect() as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            run_atomic(
                connection,
                lambda cursor: append_audit_event(
                    cursor, event_id="stable-operation-id", event_type="integration.test",
                    entity_type="test", entity_id="duplicate",
                    occurred_at="2026-11-23T00:00:01Z", payload={"attempt": 2},
                ),
            )

    with connect() as connection:
        rows = connection.execute(
            "SELECT sequence, event_id FROM audit_events ORDER BY sequence"
        ).fetchall()
        head = connection.execute(
            "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()
    assert rows == [(1, "stable-operation-id")]
    assert head == (1, first["event_hash"])



def test_postgres_specialized_learning_evidence_is_current_and_claim_linked(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('story', 'story-evidence', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "story-evidence", "status": "active"}),),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('claim', 'claim-evidence', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({
                "id": "claim-evidence", "story_id": "story-evidence",
                "evidence_ids": ["evidence-1"],
                "provenance": [{
                    "source_id": "source-1", "article_id": "article-1",
                    "observation_id": "observation-1"
                }]
            }),),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('evidence', 'evidence-1', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({
                "id": "evidence-1", "claim_id": "claim-evidence",
                "source_id": "source-1", "article_id": "article-1",
                "observation_id": "observation-1", "relationship": "supports",
                "locator": {"uri": "https://example.test/source#claim"}
            }),),
        )
        connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES ('vocabulary-evidence', 'story-evidence', 'vocabulary', 'en', "
            "'draft', 1, '2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', "
            "'editor', 'source-backed vocabulary', %s)",
            (json.dumps({
                "claim_ids": ["claim-evidence"],
                "content": {
                    "term": "resilience", "source_evidence_id": "evidence-1",
                    "source_evidence_revision": 1
                }
            }),),
        )
        connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES ('question-evidence', 'story-evidence', 'comprehension_question', "
            "'en', 'draft', 1, '2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', "
            "'editor', 'evidence-backed question', %s)",
            (json.dumps({
                "claim_ids": ["claim-evidence"],
                "content": {
                    "question": "What does the source support?",
                    "answer_claim_ids": ["claim-evidence"],
                    "answer_evidence": [{
                        "claim_id": "claim-evidence", "evidence_id": "evidence-1",
                        "evidence_revision": 1
                    }]
                }
            }),),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="current and linked"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO learning_objects "
                    "(id, story_id, kind, language, status, revision, created_at, updated_at, "
                    "last_actor, last_reason, payload_json) "
                    "VALUES ('stale-vocabulary', 'story-evidence', 'vocabulary', 'en', "
                    "'draft', 1, '2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', "
                    "'editor', 'stale evidence reference', %s)",
                    (json.dumps({
                        "claim_ids": ["claim-evidence"],
                        "content": {
                            "term": "resilience", "source_evidence_id": "evidence-1",
                            "source_evidence_revision": 2
                        }
                    }),),
                )
        with pytest.raises(psycopg.errors.CheckViolation, match="cover every answer claim"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO learning_objects "
                    "(id, story_id, kind, language, status, revision, created_at, updated_at, "
                    "last_actor, last_reason, payload_json) "
                    "VALUES ('uncovered-question', 'story-evidence', 'analysis_question', "
                    "'en', 'draft', 1, '2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', "
                    "'editor', 'missing answer provenance', %s)",
                    (json.dumps({
                        "claim_ids": ["claim-evidence"],
                        "content": {
                            "question": "Explain the claim.",
                            "answer_claim_ids": ["claim-evidence"],
                            "answer_evidence": [{
                                "claim_id": "different-claim", "evidence_id": "evidence-1",
                                "evidence_revision": 1
                            }]
                        }
                    }),),
                )

    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM learning_objects "
            "WHERE id IN ('vocabulary-evidence', 'question-evidence')"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM learning_object_revisions "
            "WHERE learning_object_id IN ('vocabulary-evidence', 'question-evidence')"
        ).fetchone()[0] == 2



def test_postgres_pipeline_run_revisions_preserve_identity_and_legal_transitions(postgres_schema):
    connect = postgres_schema
    initial = {
        "schema_version": "1.0.0",
        "id": "pipeline-run-1",
        "status": "queued",
        "started_at": "2026-11-23T00:00:00Z",
        "software_version": "engine-2.0",
        "config_hash": "sha256:" + "1" * 64,
        "input_hashes": ["sha256:" + "2" * 64],
        "initiated_by": "operator-1",
        "attempt": 1
    }
    running = {**initial, "status": "running"}
    with connect() as connection:
        connection.execute(
            "INSERT INTO pipeline_runs (id, status, revision, started_at, payload_json) "
            "VALUES (%s, %s, 1, %s, %s)",
            (initial["id"], initial["status"], initial["started_at"], json.dumps(initial)),
        )
        connection.execute(
            "INSERT INTO pipeline_run_revisions "
            "(pipeline_run_id, revision, created_at, actor, reason, payload_json) "
            "VALUES (%s, 1, %s, 'operator-1', 'created run', %s)",
            (initial["id"], initial["started_at"], json.dumps(initial)),
        )
        connection.execute(
            "UPDATE pipeline_runs SET status = 'running', revision = 2, payload_json = %s "
            "WHERE id = 'pipeline-run-1'",
            (json.dumps(running),),
        )
        connection.execute(
            "INSERT INTO pipeline_run_revisions "
            "(pipeline_run_id, revision, created_at, actor, reason, payload_json) "
            "VALUES (%s, 2, '2026-11-23T00:01:00Z', 'worker-1', 'worker started', %s)",
            (initial["id"], json.dumps(running)),
        )
        assert connection.execute(
            "SELECT revision FROM pipeline_run_revisions "
            "WHERE pipeline_run_id = 'pipeline-run-1' ORDER BY revision"
        ).fetchall() == [(1,), (2,)]

        changed_inputs = {
            **running,
            "input_hashes": ["sha256:" + "3" * 64]
        }
        with pytest.raises(psycopg.errors.CheckViolation, match="identity and inputs are immutable"):
            with connection.transaction():
                connection.execute(
                    "UPDATE pipeline_runs SET revision = 3, payload_json = %s "
                    "WHERE id = 'pipeline-run-1'",
                    (json.dumps(changed_inputs),),
                )

        invalid_transition = {**running, "status": "queued"}
        with pytest.raises(psycopg.errors.CheckViolation, match="invalid pipeline run status transition"):
            with connection.transaction():
                connection.execute(
                    "UPDATE pipeline_runs SET status = 'queued', revision = 3, payload_json = %s "
                    "WHERE id = 'pipeline-run-1'",
                    (json.dumps(invalid_transition),),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE pipeline_run_revisions SET reason = 'rewritten' "
                "WHERE pipeline_run_id = 'pipeline-run-1' AND revision = 1"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM pipeline_runs WHERE id = 'pipeline-run-1'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT revision, status FROM pipeline_runs WHERE id = 'pipeline-run-1'"
        ).fetchone() == (2, "running")



def test_postgres_stage_checkpoints_are_idempotent_revisioned_and_immutable(postgres_schema):
    connect = postgres_schema
    run = {
        "schema_version": "1.0.0", "id": "checkpoint-run",
        "status": "queued", "started_at": "2026-11-23T00:00:00Z",
        "software_version": "engine-2.0",
        "config_hash": "sha256:" + "1" * 64,
        "input_hashes": ["sha256:" + "2" * 64], "initiated_by": "operator-1"
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO pipeline_runs (id, status, revision, started_at, payload_json) "
            "VALUES (%s, 'queued', 1, %s, %s)",
            (run["id"], run["started_at"], json.dumps(run)),
        )
        connection.execute(
            "INSERT INTO pipeline_stage_checkpoints "
            "(pipeline_run_id, stage_ordinal, stage_name, input_fingerprint, idempotency_key, "
            "status, revision, created_at, updated_at, actor, reason, metadata_json) "
            "VALUES ('checkpoint-run', 0, 'collect', %s, %s, 'executing', 1, "
            "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', 'worker-1', "
            "'stage started', %s)",
            ("sha256:" + "3" * 64, "sha256:" + "4" * 64,
             json.dumps({"adapter_version": "v1", "attempt": 1})),
        )
        connection.execute(
            "UPDATE pipeline_stage_checkpoints SET status = 'indeterminate', revision = 2, "
            "provider_namespace = NULL, provider_operation_ref = 'provider-op-1', "
            "updated_at = '2026-11-23T00:01:00Z', actor = 'worker-1', "
            "reason = 'provider outcome uncertain', metadata_json = %s "
            "WHERE pipeline_run_id = 'checkpoint-run' AND stage_ordinal = 0",
            (json.dumps({"provider_state": "unknown", "attempt": 1}),),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="immutable once recorded"):
            with connection.transaction():
                connection.execute(
                    "UPDATE pipeline_stage_checkpoints SET status = 'executing', revision = 3, "
                    "provider_operation_ref = 'provider-op-2', "
                    "updated_at = '2026-11-23T00:02:00Z', actor = 'worker-1', "
                    "reason = 'attempt to replace provider operation', metadata_json = %s "
                    "WHERE pipeline_run_id = 'checkpoint-run' AND stage_ordinal = 0",
                    (json.dumps({"attempt": 2}),),
                )
        connection.execute(
            "UPDATE pipeline_stage_checkpoints SET status = 'succeeded', revision = 3, "
            "output_fingerprint = %s, updated_at = '2026-11-23T00:03:00Z', "
            "actor = 'worker-1', reason = 'provider result reconciled', metadata_json = %s "
            "WHERE pipeline_run_id = 'checkpoint-run' AND stage_ordinal = 0",
            ("sha256:" + "5" * 64, json.dumps({"reconciliation": "confirmed", "attempt": 2})),
        )
        assert connection.execute(
            "SELECT revision, status, provider_operation_ref FROM pipeline_stage_checkpoints "
            "WHERE pipeline_run_id = 'checkpoint-run' AND stage_ordinal = 0"
        ).fetchone() == (3, "succeeded", "provider-op-1")
        assert connection.execute(
            "SELECT revision, status FROM pipeline_stage_checkpoint_revisions "
            "WHERE pipeline_run_id = 'checkpoint-run' ORDER BY revision"
        ).fetchall() == [(1, "executing"), (2, "indeterminate"), (3, "succeeded")]

        with pytest.raises(psycopg.errors.CheckViolation, match="unsupported fields or types"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO pipeline_stage_checkpoints "
                    "(pipeline_run_id, stage_ordinal, stage_name, input_fingerprint, idempotency_key, "
                    "status, revision, created_at, updated_at, actor, reason, metadata_json) "
                    "VALUES ('checkpoint-run', 1, 'transform', %s, %s, 'executing', 1, "
                    "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', 'worker-1', "
                    "'invalid metadata', %s)",
                    ("sha256:" + "6" * 64, "sha256:" + "7" * 64,
                     json.dumps({"unapproved_field": "no"})),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM pipeline_stage_checkpoints "
                "WHERE pipeline_run_id = 'checkpoint-run' AND stage_ordinal = 0"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE pipeline_stage_checkpoint_revisions SET reason = 'rewritten' "
                "WHERE pipeline_run_id = 'checkpoint-run' AND stage_ordinal = 0 AND revision = 1"
            )
        connection.rollback()



def test_postgres_worker_leases_fence_reacquisition_and_preserve_history(postgres_schema):
    connect = postgres_schema
    run = {
        "schema_version": "1.0.0", "id": "lease-run",
        "status": "queued", "started_at": "2026-11-23T00:00:00Z",
        "software_version": "engine-2.0",
        "config_hash": "sha256:" + "1" * 64,
        "input_hashes": ["sha256:" + "2" * 64], "initiated_by": "operator-1"
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO pipeline_runs (id, status, revision, started_at, payload_json) "
            "VALUES (%s, 'queued', 1, %s, %s)",
            (run["id"], run["started_at"], json.dumps(run)),
        )
        connection.execute(
            "INSERT INTO pipeline_worker_leases "
            "(pipeline_run_id, worker_id, token_hash, fencing_token, status, acquired_at, "
            "heartbeat_at, expires_at, revision, updated_at, actor, reason) "
            "VALUES ('lease-run', 'worker-1', %s, 1, 'active', "
            "'2026-11-23T00:00:00Z', '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:10:00Z', 1, '2026-11-23T00:00:00Z', "
            "'worker-1', 'lease acquired')",
            ("sha256:" + "3" * 64,),
        )
        connection.execute(
            "UPDATE pipeline_worker_leases SET revision = 2, "
            "heartbeat_at = '2026-11-23T00:01:00Z', expires_at = '2026-11-23T00:11:00Z', "
            "updated_at = '2026-11-23T00:01:00Z', actor = 'worker-1', reason = 'heartbeat' "
            "WHERE pipeline_run_id = 'lease-run'",
        )
        connection.execute(
            "UPDATE pipeline_worker_leases SET revision = 3, status = 'released', "
            "updated_at = '2026-11-23T00:02:00Z', actor = 'worker-1', reason = 'release' "
            "WHERE pipeline_run_id = 'lease-run'",
        )
        connection.execute(
            "UPDATE pipeline_worker_leases SET revision = 4, worker_id = 'worker-2', "
            "token_hash = %s, fencing_token = 2, status = 'active', "
            "acquired_at = '2026-11-23T00:03:00Z', heartbeat_at = '2026-11-23T00:03:00Z', "
            "expires_at = '2026-11-23T00:13:00Z', updated_at = '2026-11-23T00:03:00Z', "
            "actor = 'worker-2', reason = 'reacquire after release' "
            "WHERE pipeline_run_id = 'lease-run'",
            ("sha256:" + "4" * 64,),
        )
        assert connection.execute(
            "SELECT worker_id, fencing_token, status, revision "
            "FROM pipeline_worker_leases WHERE pipeline_run_id = 'lease-run'"
        ).fetchone() == ("worker-2", 2, "active", 4)
        assert connection.execute(
            "SELECT revision, worker_id, fencing_token, status "
            "FROM pipeline_worker_lease_revisions "
            "WHERE pipeline_run_id = 'lease-run' ORDER BY revision"
        ).fetchall() == [
            (1, "worker-1", 1, "active"),
            (2, "worker-1", 1, "active"),
            (3, "worker-1", 1, "released"),
            (4, "worker-2", 2, "active"),
        ]
        with pytest.raises(psycopg.errors.CheckViolation, match="invalid worker lease ownership"):
            with connection.transaction():
                connection.execute(
                    "UPDATE pipeline_worker_leases SET revision = 5, worker_id = 'worker-3', "
                    "token_hash = %s, fencing_token = 3, heartbeat_at = '2026-11-23T00:04:00Z', "
                    "expires_at = '2026-11-23T00:14:00Z', updated_at = '2026-11-23T00:04:00Z', "
                    "actor = 'worker-3', reason = 'invalid takeover of active lease' "
                    "WHERE pipeline_run_id = 'lease-run'",
                    ("sha256:" + "5" * 64,),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM pipeline_worker_leases WHERE pipeline_run_id = 'lease-run'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM pipeline_worker_lease_revisions "
                "WHERE pipeline_run_id = 'lease-run' AND revision = 1"
            )
        connection.rollback()



def test_postgres_edition_artifacts_and_human_approval_decisions_are_bound_and_immutable(postgres_schema):
    connect = postgres_schema
    fingerprint = "sha256:" + "a" * 64
    manifest = {
        "manifest_id": "manifest-edition-1",
        "edition_id": "edition-1",
        "edition_revision": 1,
        "payload_sha256": fingerprint,
        "target": "staging"
    }
    decision = {
        "schema_version": "1.0.0",
        "decision_id": "decision-edition-1",
        "manifest_id": "manifest-edition-1",
        "edition_id": "edition-1",
        "edition_revision": 1,
        "payload_sha256": fingerprint,
        "decision": "approve",
        "scope": "staging_candidate_review",
        "principal_id": "approver-1",
        "role": "edition_approver",
        "decided_at": "2026-11-23T00:01:00Z",
        "reason": "independent staging review",
        "identity_attestation": {"issuer": "https://identity.example"},
        "publication_authorized": False
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, principal_kind) "
            "VALUES ('approver-1', 'Human Approver', %s, 1, '2026-11-23T00:00:00Z', "
            "'admin', 'test principal', 'human')",
            (json.dumps(["edition_approver"]),),
        )
        connection.execute(
            "INSERT INTO edition_artifacts "
            "(manifest_id, edition_id, edition_revision, payload_sha256, payload_json, "
            "manifest_json, verification_json, created_at, actor, reason) "
            "VALUES ('manifest-edition-1', 'edition-1', 1, %s, %s, %s, %s, "
            "'2026-11-23T00:00:00Z', 'verifier', 'freshly verified artifact')",
            (
                fingerprint,
                json.dumps({"edition_id": "edition-1", "stories": []}),
                json.dumps(manifest),
                json.dumps({"verified": True, "publication_authorized": False}),
            ),
        )
        connection.execute(
            "INSERT INTO edition_decisions "
            "(decision_id, manifest_id, edition_id, edition_revision, payload_sha256, "
            "decision, principal_id, decided_at, payload_json) "
            "VALUES ('decision-edition-1', 'manifest-edition-1', 'edition-1', 1, %s, "
            "'approve', 'approver-1', '2026-11-23T00:01:00Z', %s)",
            (fingerprint, json.dumps(decision)),
        )
        assert connection.execute(
            "SELECT decision, principal_id FROM edition_decisions "
            "WHERE decision_id = 'decision-edition-1'"
        ).fetchone() == ("approve", "approver-1")

        inactive_decision = {
            **decision, "decision_id": "decision-inactive",
            "manifest_id": "manifest-edition-2", "edition_revision": 2,
            "principal_id": "inactive-approver"
        }
        manifest2 = {
            **manifest, "manifest_id": "manifest-edition-2", "edition_revision": 2
        }
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, principal_kind) "
            "VALUES ('inactive-approver', 'Inactive Approver', %s, 0, "
            "'2026-11-23T00:00:00Z', 'admin', 'inactive test principal', 'human')",
            (json.dumps(["edition_approver"]),),
        )
        connection.execute(
            "INSERT INTO edition_artifacts "
            "(manifest_id, edition_id, edition_revision, payload_sha256, payload_json, "
            "manifest_json, verification_json, created_at, actor, reason) "
            "VALUES ('manifest-edition-2', 'edition-1', 2, %s, %s, %s, %s, "
            "'2026-11-23T00:00:00Z', 'verifier', 'second artifact')",
            (
                fingerprint,
                json.dumps({"edition_id": "edition-1", "stories": []}),
                json.dumps(manifest2),
                json.dumps({"verified": True}),
            ),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="active human approver"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO edition_decisions "
                    "(decision_id, manifest_id, edition_id, edition_revision, payload_sha256, "
                    "decision, principal_id, decided_at, payload_json) "
                    "VALUES ('decision-inactive', 'manifest-edition-2', 'edition-1', 2, %s, "
                    "'approve', 'inactive-approver', '2026-11-23T00:01:00Z', %s)",
                    (fingerprint, json.dumps(inactive_decision)),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE edition_artifacts SET reason = 'rewritten' "
                "WHERE manifest_id = 'manifest-edition-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM edition_decisions WHERE decision_id = 'decision-edition-1'"
            )
        connection.rollback()



def test_postgres_edition_release_authorization_requires_independent_human_authority(postgres_schema):
    connect = postgres_schema
    fingerprint = "sha256:" + "b" * 64
    manifest = {
        "manifest_id": "manifest-release-1", "edition_id": "edition-release",
        "edition_revision": 1, "payload_sha256": fingerprint
    }
    approval = {
        "schema_version": "1.0.0", "decision_id": "decision-release-1",
        "manifest_id": "manifest-release-1", "edition_id": "edition-release",
        "edition_revision": 1, "payload_sha256": fingerprint,
        "decision": "approve", "scope": "staging_candidate_review",
        "principal_id": "release-approver", "role": "edition_approver",
        "decided_at": "2026-11-23T00:01:00Z", "reason": "approved",
        "identity_attestation": {"issuer": "https://identity.example"},
        "publication_authorized": False
    }
    authorization = {
        "schema_version": "1.0.0", "authorization_id": "release-auth-1",
        "manifest_id": "manifest-release-1", "edition_id": "edition-release",
        "edition_revision": 1, "payload_sha256": fingerprint,
        "approval_decision_id": "decision-release-1",
        "approver_principal_id": "release-approver",
        "authorizer_principal_id": "release-authorizer",
        "authorized_at": "2026-11-23T00:02:00Z",
        "scope": "controlled_release_gate", "release_authorized": True,
        "publication_authorized": False
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, principal_kind) "
            "VALUES ('release-approver', 'Approver', %s, 1, '2026-11-23T00:00:00Z', "
            "'admin', 'approver', 'human'), "
            "('release-authorizer', 'Authorizer', %s, 1, '2026-11-23T00:00:00Z', "
            "'admin', 'authorizer', 'human')",
            (json.dumps(["edition_approver"]), json.dumps(["edition_release_authorizer"])),
        )
        connection.execute(
            "INSERT INTO edition_artifacts "
            "(manifest_id, edition_id, edition_revision, payload_sha256, payload_json, "
            "manifest_json, verification_json, created_at, actor, reason) "
            "VALUES ('manifest-release-1', 'edition-release', 1, %s, %s, %s, %s, "
            "'2026-11-23T00:00:00Z', 'verifier', 'verified for release gate')",
            (
                fingerprint, json.dumps({"edition_id": "edition-release"}),
                json.dumps(manifest), json.dumps({"verified": True})
            ),
        )
        connection.execute(
            "INSERT INTO edition_decisions "
            "(decision_id, manifest_id, edition_id, edition_revision, payload_sha256, "
            "decision, principal_id, decided_at, payload_json) "
            "VALUES ('decision-release-1', 'manifest-release-1', 'edition-release', 1, %s, "
            "'approve', 'release-approver', '2026-11-23T00:01:00Z', %s)",
            (fingerprint, json.dumps(approval)),
        )
        connection.execute(
            "INSERT INTO edition_release_authorizations "
            "(authorization_id, manifest_id, edition_id, edition_revision, payload_sha256, "
            "approval_decision_id, approver_principal_id, authorizer_principal_id, "
            "authorized_at, payload_json) "
            "VALUES ('release-auth-1', 'manifest-release-1', 'edition-release', 1, %s, "
            "'decision-release-1', 'release-approver', 'release-authorizer', "
            "'2026-11-23T00:02:00Z', %s)",
            (fingerprint, json.dumps(authorization)),
        )
        assert connection.execute(
            "SELECT approver_principal_id, authorizer_principal_id "
            "FROM edition_release_authorizations WHERE authorization_id = 'release-auth-1'"
        ).fetchone() == ("release-approver", "release-authorizer")
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO edition_release_authorizations "
                    "(authorization_id, manifest_id, edition_id, edition_revision, payload_sha256, "
                    "approval_decision_id, approver_principal_id, authorizer_principal_id, "
                    "authorized_at, payload_json) "
                    "VALUES ('release-auth-same-person', 'manifest-release-1', 'edition-release', 1, %s, "
                    "'decision-release-1', 'release-approver', 'release-approver', "
                    "'2026-11-23T00:03:00Z', %s)",
                    (fingerprint, json.dumps({
                        **authorization,
                        "authorization_id": "release-auth-same-person",
                        "authorizer_principal_id": "release-approver",
                        "authorized_at": "2026-11-23T00:03:00Z"
                    })),
                )

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM edition_release_authorizations "
                "WHERE authorization_id = 'release-auth-1'"
            )
        connection.rollback()


def test_postgres_edition_domain_snapshots_cannot_bypass_controlled_publication(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match="controlled release workflow"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO domain_records "
                    "(record_type, id, revision, created_at, updated_at, payload_json) "
                    "VALUES ('edition', 'edition-published', 1, '2026-11-23T00:00:00Z', "
                    "'2026-11-23T00:00:00Z', %s)",
                    (json.dumps({"id": "edition-published", "status": "published"}),),
                )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json) "
            "VALUES ('edition', 'edition-draft', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s)",
            (json.dumps({"id": "edition-draft", "status": "draft"}),),
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            with connection.transaction():
                connection.execute(
                    "UPDATE domain_records SET revision = 2 "
                    "WHERE record_type = 'edition' AND id = 'edition-draft'"
                )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            with connection.transaction():
                connection.execute(
                    "DELETE FROM domain_records "
                    "WHERE record_type = 'edition' AND id = 'edition-draft'"
                )



def test_postgres_staging_event_binds_three_human_authorities_and_advances_state(postgres_schema):
    connect = postgres_schema
    fingerprint = "sha256:" + "c" * 64
    export_fingerprint = "sha256:" + "d" * 64
    manifest = {
        "manifest_id": "manifest-stage-1", "edition_id": "edition-stage",
        "edition_revision": 1, "payload_sha256": fingerprint
    }
    approval = {
        "schema_version": "1.0.0", "decision_id": "decision-stage-1",
        "manifest_id": "manifest-stage-1", "edition_id": "edition-stage",
        "edition_revision": 1, "payload_sha256": fingerprint,
        "decision": "approve", "scope": "staging_candidate_review",
        "principal_id": "stage-approver", "role": "edition_approver",
        "decided_at": "2026-11-23T00:01:00Z", "reason": "approved for staging",
        "identity_attestation": {"issuer": "https://identity.example"},
        "publication_authorized": False
    }
    authorization = {
        "schema_version": "1.0.0", "authorization_id": "stage-release-auth",
        "manifest_id": "manifest-stage-1", "edition_id": "edition-stage",
        "edition_revision": 1, "payload_sha256": fingerprint,
        "approval_decision_id": "decision-stage-1",
        "approver_principal_id": "stage-approver",
        "authorizer_principal_id": "stage-authorizer",
        "authorized_at": "2026-11-23T00:02:00Z",
        "scope": "controlled_release_gate", "release_authorized": True,
        "publication_authorized": False
    }
    event_payload = {
        "event_id": "stage-event-1", "state_revision": 1, "channel": "staging",
        "operation": "promote", "manifest_id": "manifest-stage-1",
        "edition_id": "edition-stage", "edition_revision": 1,
        "payload_sha256": fingerprint, "export_id": "export-stage-1",
        "export_sha256": export_fingerprint,
        "previous_current_export_id": None, "lkg_export_id": None,
        "operator_principal_id": "stage-operator",
        "occurred_at": "2026-11-23T00:03:00Z", "reason": "first staging promotion",
        "publication_authorized": False, "target": "staging",
        "staging_decision": {
            "decision_id": "decision-stage-1", "principal_id": "stage-approver"
        },
        "release_authorization": {
            "authorization_id": "stage-release-auth",
            "authorizer_principal_id": "stage-authorizer"
        }
    }
    export_package = {
        "export_id": "export-stage-1", "manifest_id": "manifest-stage-1",
        "payload_sha256": fingerprint, "target": "staging",
        "publication_authorized": False
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, principal_kind) "
            "VALUES ('stage-approver', 'Approver', %s, 1, '2026-11-23T00:00:00Z', 'admin', 'approver', 'human'), "
            "('stage-authorizer', 'Authorizer', %s, 1, '2026-11-23T00:00:00Z', 'admin', 'authorizer', 'human'), "
            "('stage-operator', 'Operator', %s, 1, '2026-11-23T00:00:00Z', 'admin', 'operator', 'human')",
            (
                json.dumps(["edition_approver"]),
                json.dumps(["edition_release_authorizer"]),
                json.dumps(["edition_staging_operator"])
            ),
        )
        connection.execute(
            "INSERT INTO edition_artifacts "
            "(manifest_id, edition_id, edition_revision, payload_sha256, payload_json, "
            "manifest_json, verification_json, created_at, actor, reason) "
            "VALUES ('manifest-stage-1', 'edition-stage', 1, %s, %s, %s, %s, "
            "'2026-11-23T00:00:00Z', 'verifier', 'verified staging candidate')",
            (
                fingerprint, json.dumps({"edition_id": "edition-stage"}),
                json.dumps(manifest), json.dumps({"verified": True})
            ),
        )
        connection.execute(
            "INSERT INTO edition_decisions "
            "(decision_id, manifest_id, edition_id, edition_revision, payload_sha256, "
            "decision, principal_id, decided_at, payload_json) "
            "VALUES ('decision-stage-1', 'manifest-stage-1', 'edition-stage', 1, %s, "
            "'approve', 'stage-approver', '2026-11-23T00:01:00Z', %s)",
            (fingerprint, json.dumps(approval)),
        )
        connection.execute(
            "INSERT INTO edition_release_authorizations "
            "(authorization_id, manifest_id, edition_id, edition_revision, payload_sha256, "
            "approval_decision_id, approver_principal_id, authorizer_principal_id, "
            "authorized_at, payload_json) "
            "VALUES ('stage-release-auth', 'manifest-stage-1', 'edition-stage', 1, %s, "
            "'decision-stage-1', 'stage-approver', 'stage-authorizer', "
            "'2026-11-23T00:02:00Z', %s)",
            (fingerprint, json.dumps(authorization)),
        )
        connection.execute(
            "INSERT INTO edition_staging_events "
            "(event_id, state_revision, channel, operation, manifest_id, edition_id, "
            "edition_revision, payload_sha256, export_id, export_sha256, "
            "previous_current_export_id, lkg_export_id, operator_principal_id, "
            "occurred_at, reason, export_package_json, payload_json) "
            "VALUES ('stage-event-1', 1, 'staging', 'promote', 'manifest-stage-1', "
            "'edition-stage', 1, %s, 'export-stage-1', %s, NULL, NULL, "
            "'stage-operator', '2026-11-23T00:03:00Z', 'first staging promotion', %s, %s)",
            (
                fingerprint, export_fingerprint,
                json.dumps(export_package), json.dumps(event_payload)
            ),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="staging state must advance atomically"):
            with connection.transaction():
                connection.execute(
                    "UPDATE edition_staging_channel_state SET state_revision = 2, "
                    "current_event_id = 'stage-event-1', current_export_id = 'export-stage-1' "
                    "WHERE channel = 'staging'"
                )
        connection.execute(
            "UPDATE edition_staging_channel_state SET state_revision = 1, "
            "current_event_id = 'stage-event-1', current_export_id = 'export-stage-1' "
            "WHERE channel = 'staging'"
        )
        assert connection.execute(
            "SELECT state_revision, current_event_id, current_export_id "
            "FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone() == (1, "stage-event-1", "export-stage-1")

    with connect() as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE edition_staging_events SET reason = 'rewritten' "
                "WHERE event_id = 'stage-event-1'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM edition_staging_channel_state WHERE channel = 'staging'"
            )
        connection.rollback()



def test_postgres_domain_record_updates_require_monotonic_revisions_and_snapshot_history(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('story', 'domain-revision-1', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s, 'editor', 'initial', '2026-11-23T00:00:00Z')",
            (json.dumps({"id": "domain-revision-1", "revision": 1, "status": "draft"}),),
        )
        connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('story', 'domain-revision-1', 1, '2026-11-23T00:00:00Z', "
            "'editor', 'initial', %s)",
            (json.dumps({"id": "domain-revision-1", "revision": 1, "status": "draft"}),),
        )
        connection.execute(
            "UPDATE domain_records SET revision = 2, updated_at = '2026-11-23T00:01:00Z', "
            "payload_json = %s, last_actor = 'reviewer', last_reason = 'approved', "
            "last_revision_at = '2026-11-23T00:01:00Z' "
            "WHERE record_type = 'story' AND id = 'domain-revision-1'",
            (json.dumps({"id": "domain-revision-1", "revision": 2, "status": "approved"}),),
        )
        assert connection.execute(
            "SELECT revision, actor, reason FROM domain_record_revisions "
            "WHERE record_type = 'story' AND record_id = 'domain-revision-1' ORDER BY revision"
        ).fetchall() == [(1, "editor", "initial"), (2, "reviewer", "approved")]

        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('article', 'article-immutable', 1, '2026-11-23T00:00:00Z', "
            "'2026-11-23T00:00:00Z', %s, 'editor', 'initial article', '2026-11-23T00:00:00Z')",
            (json.dumps({
                "id": "article-immutable", "source_id": "source-original",
                "canonical_uri": "https://example.test/original"
            }),),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="article source and canonical identity are immutable"):
            with connection.transaction():
                connection.execute(
                    "UPDATE domain_records SET revision = 2, payload_json = %s, "
                    "last_actor = 'editor', last_reason = 'attempt source swap', "
                    "last_revision_at = '2026-11-23T00:02:00Z' "
                    "WHERE record_type = 'article' AND id = 'article-immutable'",
                    (json.dumps({
                        "id": "article-immutable", "source_id": "source-changed",
                        "canonical_uri": "https://example.test/original"
                    }),),
                )

        with pytest.raises(psycopg.errors.CheckViolation, match="revision must increment by one"):
            with connection.transaction():
                connection.execute(
                    "UPDATE domain_records SET revision = 4, payload_json = %s, "
                    "last_actor = 'editor', last_reason = 'skip revision', "
                    "last_revision_at = '2026-11-23T00:03:00Z' "
                    "WHERE record_type = 'story' AND id = 'domain-revision-1'",
                    (json.dumps({"id": "domain-revision-1", "revision": 4, "status": "approved"}),),
                )


def _seed_atomic_staging_case(connection):
    from datetime import datetime, timedelta, timezone
    from sngso_engine.benchmark_access import AuthenticatedIdentity

    now = datetime(2026, 11, 23, 0, 4, tzinfo=timezone.utc)
    operator_identity = AuthenticatedIdentity(
        principal_id="principal:atomic-operator",
        issuer="https://identity.example",
        audience="sarangsho-editorial",
        authenticated_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
        session_id="principal:atomic-operator-session",
        subject="oidc|principal:atomic-operator",
        authentication_methods=("pwd", "mfa"),
        authentication_context="urn:example:loa:2",
        provider_key_id="key-1",
        provider_adapter="test-oidc",
    )
    principals = (
        ("principal:atomic-approver", ["edition_approver"]),
        ("principal:atomic-authorizer", ["edition_release_authorizer"]),
        ("principal:atomic-operator", ["edition_staging_operator"]),
    )
    for principal_id, roles in principals:
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, principal_kind) "
            "VALUES (%s, %s, %s, 1, %s, 'test-admin', 'integration fixture', 'human')",
            (principal_id, principal_id, json.dumps(roles), now.isoformat()),
        )

    edition_payload = {
        "edition_id": "edition:atomic",
        "edition_revision": 1,
        "edition_key": "2026-11-23",
    }
    payload_sha = sha256_fingerprint(edition_payload)
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": "manifest:atomic",
        "edition_id": "edition:atomic",
        "edition_revision": 1,
        "edition_key": "2026-11-23",
        "created_at": now.isoformat(),
        "pipeline_run_id": "pipeline:atomic",
        "payload_sha256": payload_sha,
        "story_bindings": [{
            "story_id": "story:atomic",
            "story_revision": 1,
            "story_snapshot_sha256": "sha256:" + "a" * 64,
            "learning_objects": [
                {
                    "learning_object_id": "learning:atomic-bn",
                    "learning_object_revision": 1,
                    "learning_object_sha256": "sha256:" + "b" * 64,
                    "kind": "headline_summary",
                    "language": "bn",
                },
                {
                    "learning_object_id": "learning:atomic-en",
                    "learning_object_revision": 1,
                    "learning_object_sha256": "sha256:" + "c" * 64,
                    "kind": "headline_summary",
                    "language": "en",
                },
            ],
        }],
        "source_permissions": [{
            "source_id": "source:atomic",
            "endpoint_id": "endpoint:atomic",
            "grant_id": "grant:atomic",
            "operation": "republication",
        }],
    }
    connection.execute(
        "INSERT INTO edition_artifacts "
        "(manifest_id, edition_id, edition_revision, payload_sha256, payload_json, "
        "manifest_json, verification_json, created_at, actor, reason) "
        "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, 'test-verifier', 'verified fixture')",
        (
            "manifest:atomic", "edition:atomic", payload_sha,
            json.dumps(edition_payload), json.dumps(manifest),
            json.dumps({"publication_authorized": False}),
            now.isoformat(),
        ),
    )
    identity_attestation = {
        "issuer": "https://identity.example",
        "audience": "sarangsho-editorial",
        "subject": "oidc|principal:atomic-approver",
        "authenticated_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "authentication_methods": ["pwd", "mfa"],
        "authentication_context": "urn:example:loa:2",
        "provider_key_id": "key-1",
        "provider_adapter": "test-oidc",
        "session_fingerprint": "sha256:" + "d" * 64,
    }
    approval = {
        "schema_version": "1.0.0",
        "decision_id": "decision:atomic",
        "manifest_id": "manifest:atomic",
        "edition_id": "edition:atomic",
        "edition_revision": 1,
        "payload_sha256": payload_sha,
        "decision": "approve",
        "scope": "staging_candidate_review",
        "principal_id": "principal:atomic-approver",
        "role": "edition_approver",
        "decided_at": now.isoformat(),
        "reason": "approved for staging",
        "identity_attestation": identity_attestation,
        "publication_authorized": False,
    }
    connection.execute(
        "INSERT INTO edition_decisions "
        "(decision_id, manifest_id, edition_id, edition_revision, payload_sha256, "
        "decision, principal_id, decided_at, payload_json) "
        "VALUES (%s, %s, %s, 1, %s, 'approve', %s, %s, %s)",
        (
            "decision:atomic", "manifest:atomic", "edition:atomic", payload_sha,
            "principal:atomic-approver", now.isoformat(), json.dumps(approval),
        ),
    )
    authorization = {
        "schema_version": "1.0.0",
        "authorization_id": "authorization:atomic",
        "manifest_id": "manifest:atomic",
        "edition_id": "edition:atomic",
        "edition_revision": 1,
        "payload_sha256": payload_sha,
        "approval_decision_id": "decision:atomic",
        "approver_principal_id": "principal:atomic-approver",
        "authorizer_principal_id": "principal:atomic-authorizer",
        "authorized_at": now.isoformat(),
        "scope": "controlled_release_gate",
        "reason": "independent release authorization",
        "identity_attestation": {
            **identity_attestation,
            "subject": "oidc|principal:atomic-authorizer",
        },
        "release_authorized": True,
        "publication_authorized": False,
    }
    connection.execute(
        "INSERT INTO edition_release_authorizations "
        "(authorization_id, manifest_id, edition_id, edition_revision, payload_sha256, "
        "approval_decision_id, approver_principal_id, authorizer_principal_id, "
        "authorized_at, payload_json) "
        "VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s)",
        (
            "authorization:atomic", "manifest:atomic", "edition:atomic", payload_sha,
            "decision:atomic", "principal:atomic-approver", "principal:atomic-authorizer",
            now.isoformat(), json.dumps(authorization),
        ),
    )
    package = {
        "schema_version": "1.0.0",
        "export_id": "export:atomic-1",
        "target": "staging",
        "created_at": now.isoformat(),
        "manifest_id": "manifest:atomic",
        "edition_id": "edition:atomic",
        "edition_revision": 1,
        "payload_sha256": payload_sha,
        "edition_payload": edition_payload,
        "provenance_manifest": manifest,
        "staging_decision": approval,
        "release_authorization": authorization,
        "publication_authorized": False,
    }
    return operator_identity, now, package


def test_postgres_atomic_staging_commits_event_state_and_global_audit_together(postgres_schema):
    from sngso_engine.postgres_edition_staging import stage_edition_export

    connect = postgres_schema
    with connect() as connection:
        identity, now, package = _seed_atomic_staging_case(connection)
    with connect() as connection:
        result = stage_edition_export(
            connection,
            manifest_id="manifest:atomic",
            operation="promote",
            identity=identity,
            reason="integration staging promotion",
            expected_state_revision=0,
            reverify_export=lambda cursor, artifact, evaluated_at: package,
            event_id="atomic-stage-event",
            now=now,
        )
        assert result["state"]["state_revision"] == 1
        assert result["state"]["current_export_id"] == "export:atomic-1"
        assert result["audit"]["sequence"] == 1
        assert connection.execute(
            "SELECT state_revision, current_event_id, current_export_id "
            "FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone() == (1, "atomic-stage-event", "export:atomic-1")
        assert connection.execute(
            "SELECT count(*) FROM edition_staging_events"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT sequence, event_type, entity_id FROM audit_events"
        ).fetchone() == (1, "edition_staging.promoted", "atomic-stage-event")
        assert connection.execute(
            "SELECT sequence FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()[0] == 1


def test_postgres_atomic_staging_rolls_back_event_and_state_if_audit_append_fails(
    postgres_schema, monkeypatch
):
    import sngso_engine.postgres_edition_staging as staging

    connect = postgres_schema
    with connect() as connection:
        identity, now, package = _seed_atomic_staging_case(connection)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit append failure")

    monkeypatch.setattr(staging, "append_audit_event", fail_audit)
    with connect() as connection:
        with pytest.raises(RuntimeError, match="injected audit append failure"):
            staging.stage_edition_export(
                connection,
                manifest_id="manifest:atomic",
                operation="promote",
                identity=identity,
                reason="failure-injection promotion",
                expected_state_revision=0,
                reverify_export=lambda cursor, artifact, evaluated_at: package,
                event_id="atomic-stage-event-fail",
                now=now,
            )
        assert connection.execute(
            "SELECT state_revision, current_event_id, current_export_id "
            "FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone() == (0, None, None)
        assert connection.execute(
            "SELECT count(*) FROM edition_staging_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT sequence FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()[0] == 0


def test_postgres_atomic_staging_rejects_stale_state_before_reverification(postgres_schema):
    from sngso_engine.postgres_edition_staging import (
        PostgresStagingError, stage_edition_export,
    )

    connect = postgres_schema
    with connect() as connection:
        identity, now, package = _seed_atomic_staging_case(connection)
    callback_calls = []
    with connect() as connection:
        with pytest.raises(PostgresStagingError, match="state revision conflict"):
            stage_edition_export(
                connection,
                manifest_id="manifest:atomic",
                operation="promote",
                identity=identity,
                reason="stale request",
                expected_state_revision=7,
                reverify_export=lambda cursor, artifact, evaluated_at: callback_calls.append(True) or package,
                event_id="atomic-stage-event-stale",
                now=now,
            )
    assert callback_calls == []


def _attempt_atomic_stage(connect, identity, now, package, event_id):
    from sngso_engine.postgres_edition_staging import (
        PostgresStagingError, stage_edition_export,
    )
    with connect() as connection:
        try:
            stage_edition_export(
                connection,
                manifest_id="manifest:atomic",
                operation="promote",
                identity=identity,
                reason="concurrent promotion attempt",
                expected_state_revision=0,
                reverify_export=lambda cursor, artifact, evaluated_at: package,
                event_id=event_id,
                now=now,
            )
            return "committed"
        except PostgresStagingError as exc:
            if "state revision conflict" in str(exc):
                return "stale"
            raise


def test_postgres_atomic_staging_serializes_competing_promotions(postgres_schema):
    from concurrent.futures import ThreadPoolExecutor

    connect = postgres_schema
    with connect() as connection:
        identity, now, package = _seed_atomic_staging_case(connection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _attempt_atomic_stage,
                connect,
                identity,
                now,
                package,
                f"atomic-concurrent-event-{index}",
            )
            for index in range(2)
        ]
        outcomes = sorted(future.result() for future in futures)

    assert outcomes == ["committed", "stale"]
    with connect() as connection:
        assert connection.execute(
            "SELECT state_revision, current_export_id "
            "FROM edition_staging_channel_state WHERE channel = 'staging'"
        ).fetchone() == (1, "export:atomic-1")
        assert connection.execute(
            "SELECT count(*) FROM edition_staging_events"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT sequence FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()[0] == 1


def test_postgres_atomic_staging_rollback_restores_prior_export_and_tracks_lkg(postgres_schema):
    from sngso_engine.postgres_edition_staging import stage_edition_export

    connect = postgres_schema
    with connect() as connection:
        identity, now, first_package = _seed_atomic_staging_case(connection)
    second_package = {**first_package, "export_id": "export:atomic-2"}

    with connect() as connection:
        first = stage_edition_export(
            connection,
            manifest_id="manifest:atomic",
            operation="promote",
            identity=identity,
            reason="first promotion",
            expected_state_revision=0,
            reverify_export=lambda cursor, artifact, evaluated_at: first_package,
            event_id="atomic-first-promotion",
            now=now,
        )
        second = stage_edition_export(
            connection,
            manifest_id="manifest:atomic",
            operation="promote",
            identity=identity,
            reason="second promotion",
            expected_state_revision=1,
            reverify_export=lambda cursor, artifact, evaluated_at: second_package,
            event_id="atomic-second-promotion",
            now=now,
        )
        rolled_back = stage_edition_export(
            connection,
            manifest_id="manifest:atomic",
            operation="rollback",
            identity=identity,
            reason="restore last known good export",
            expected_state_revision=2,
            reverify_export=lambda cursor, artifact, evaluated_at: first_package,
            event_id="atomic-rollback-event",
            now=now,
        )

        assert first["state"]["current_export_id"] == "export:atomic-1"
        assert second["state"]["lkg_export_id"] == "export:atomic-1"
        assert rolled_back["state"] == {
            "channel": "staging",
            "state_revision": 3,
            "current_event_id": "atomic-rollback-event",
            "current_export_id": "export:atomic-1",
            "lkg_event_id": "atomic-second-promotion",
            "lkg_export_id": "export:atomic-2",
        }
        assert connection.execute(
            "SELECT operation FROM edition_staging_events ORDER BY state_revision"
        ).fetchall() == [("promote",), ("promote",), ("rollback",)]
        assert connection.execute(
            "SELECT sequence, event_type FROM audit_events ORDER BY sequence"
        ).fetchall() == [
            (1, "edition_staging.promoted"),
            (2, "edition_staging.promoted"),
            (3, "edition_staging.rolled_back"),
        ]
        assert connection.execute(
            "SELECT sequence FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()[0] == 3


def _seed_postgres_permission_case(connection):
    source_id = "source:rights-fixture"
    endpoint_id = "endpoint:rights-fixture"
    grant_id = "grant:rights-fixture"
    evidence_id = "evidence:rights-fixture"
    stamp = "2026-11-23T00:00:00Z"
    connection.execute(
        "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
        "VALUES (%s, 'Rights Publisher', 'active', %s, %s, %s)",
        (source_id, stamp, stamp, json.dumps({
            "id": source_id, "publisher_name": "Rights Publisher", "status": "active",
            "created_at": stamp, "updated_at": stamp,
        })),
    )
    connection.execute(
        "INSERT INTO source_endpoints "
        "(id, source_id, endpoint_uri, status, created_at, updated_at, payload_json) "
        "VALUES (%s, %s, 'https://rights.example/feed', 'active', %s, %s, %s)",
        (endpoint_id, source_id, stamp, stamp, json.dumps({
            "id": endpoint_id, "source_id": source_id,
            "endpoint_uri": "https://rights.example/feed", "status": "active",
            "created_at": stamp, "updated_at": stamp,
        })),
    )
    connection.execute(
        "INSERT INTO permission_evidence "
        "(id, source_id, review_status, content_fingerprint, captured_at, payload_json) "
        "VALUES (%s, %s, 'verified', %s, %s, %s)",
        (
            evidence_id, source_id, "sha256:" + "e" * 64, stamp,
            json.dumps({
                "id": evidence_id, "source_id": source_id,
                "review_status": "verified",
                "content_fingerprint": "sha256:" + "e" * 64,
                "captured_at": stamp,
            }),
        ),
    )
    operations = {}
    for name in (
        "discovery", "metadata", "excerpt_storage", "full_text_storage",
        "translation", "image_storage", "republication",
    ):
        if name == "republication":
            operations[name] = {
                "allowed": True, "status": "approved", "conditions": [],
                "evidence_ids": [evidence_id],
            }
        else:
            operations[name] = {
                "allowed": False, "status": "denied", "conditions": [],
            }
    grant = {
        "schema_version": "1.0.0",
        "id": grant_id,
        "source_id": source_id,
        "endpoint_ids": [endpoint_id],
        "status": "approved",
        "operations": operations,
        "evidence_ids": [evidence_id],
        "basis": "Publisher permission record for staging republication",
        "effective_from": "2026-01-01T00:00:00Z",
        "effective_until": "2027-01-01T00:00:00Z",
        "created_at": stamp,
        "reviewed_at": stamp,
        "reviewed_by": "human:rights-reviewer",
    }
    connection.execute(
        "INSERT INTO permission_grants "
        "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
        "VALUES (%s, %s, 'approved', %s, %s, 1, %s)",
        (
            grant_id, source_id, grant["effective_from"],
            grant["effective_until"], json.dumps(grant),
        ),
    )
    connection.execute(
        "INSERT INTO grant_endpoints (grant_id, source_id, endpoint_id) VALUES (%s, %s, %s)",
        (grant_id, source_id, endpoint_id),
    )
    connection.execute(
        "INSERT INTO grant_evidence (grant_id, source_id, evidence_id) VALUES (%s, %s, %s)",
        (grant_id, source_id, evidence_id),
    )
    connection.execute(
        "INSERT INTO grant_operation_evidence (grant_id, source_id, operation, evidence_id) "
        "VALUES (%s, %s, 'republication', %s)",
        (grant_id, source_id, evidence_id),
    )
    return source_id, endpoint_id, grant_id, evidence_id


def test_postgres_staging_permission_evaluator_locks_and_checks_relational_evidence(postgres_schema):
    from datetime import datetime, timezone
    from sngso_engine.postgres_permission_read import evaluate_postgres_grant_for_staging

    connect = postgres_schema
    evaluation_time = datetime(2026, 11, 23, 0, 4, tzinfo=timezone.utc)
    with connect() as connection:
        source_id, endpoint_id, grant_id, evidence_id = _seed_postgres_permission_case(connection)

    with connect() as connection:
        decision = evaluate_postgres_grant_for_staging(
            connection.cursor(), grant_id, "republication",
            endpoint_id=endpoint_id, now=evaluation_time,
        )
        assert decision.allowed is True
        assert decision.reason == "approved"
        assert decision.permission_id == grant_id
        assert decision.grant_revision == 1

        wrong_endpoint = evaluate_postgres_grant_for_staging(
            connection.cursor(), grant_id, "republication",
            endpoint_id="endpoint:other", now=evaluation_time,
        )
        assert wrong_endpoint.allowed is False
        assert wrong_endpoint.reason == "endpoint_scope_mismatch"

    with connect() as connection:
        connection.execute(
            "UPDATE permission_evidence SET review_status = 'pending' WHERE id = %s",
            (evidence_id,),
        )
    with connect() as connection:
        denied = evaluate_postgres_grant_for_staging(
            connection.cursor(), grant_id, "republication",
            endpoint_id=endpoint_id, now=evaluation_time,
        )
        assert denied.allowed is False
        assert denied.reason == "permission_evidence_not_verified"

    with connect() as connection:
        connection.execute(
            "UPDATE permission_evidence SET review_status = 'verified' WHERE id = %s",
            (evidence_id,),
        )
        connection.execute(
            "UPDATE sources SET status = 'inactive' WHERE id = %s",
            (source_id,),
        )
    with connect() as connection:
        denied_source = evaluate_postgres_grant_for_staging(
            connection.cursor(), grant_id, "republication",
            endpoint_id=endpoint_id, now=evaluation_time,
        )
        assert denied_source.allowed is False
        assert denied_source.reason == "source_not_active"


def test_postgres_edition_read_view_uses_transaction_cursor_and_checks_revision_heads(postgres_schema):
    from sngso_engine.postgres_edition_read import (
        PostgresEditionReadAdapterError, PostgresEditionReadAdapterIncomplete,
        PostgresEditionRegistryReadView,
    )

    connect = postgres_schema
    stamp = "2026-11-23T00:00:00Z"
    story_id = "story:read-view"
    article_id = "article:read-view"
    claim_id = "claim:read-view"
    evidence_id = "evidence:read-view"
    glossary_id = "glossary:read-view"
    learning_id = "learning:read-view"
    source_id = "source:read-view"
    endpoint_id = "endpoint:read-view"
    pipeline_id = "pipeline:read-view"

    with connect() as connection:
        story_payload = {
            "id": story_id, "canonical_title": "Read-view fixture", "status": "active",
            "article_ids": [article_id],
        }
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('story', %s, 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (story_id, stamp, stamp, json.dumps(story_payload), stamp),
        )
        connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('story', %s, 1, %s, 'fixture', 'initial', %s)",
            (story_id, stamp, json.dumps(story_payload)),
        )
        article_payload = {
            "id": article_id, "source_id": source_id,
            "canonical_uri": "https://read-view.example/article",
            "title": "Read-view article", "source_language": "en",
        }
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('article', %s, 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (article_id, stamp, stamp, json.dumps(article_payload), stamp),
        )
        connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('article', %s, 1, %s, 'fixture', 'initial', %s)",
            (article_id, stamp, json.dumps(article_payload)),
        )
        claim_payload = {
            "id": claim_id, "story_id": story_id,
            "text": "The central bank announced a policy decision.",
            "assessment": "supported", "evidence_ids": [evidence_id],
            "provenance": [{"source_id": source_id, "article_id": article_id}],
        }
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('claim', %s, 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (claim_id, stamp, stamp, json.dumps(claim_payload), stamp),
        )
        connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('claim', %s, 1, %s, 'fixture', 'initial', %s)",
            (claim_id, stamp, json.dumps(claim_payload)),
        )
        evidence_payload = {
            "id": evidence_id, "claim_id": claim_id, "source_id": source_id,
            "article_id": article_id, "relationship": "supports",
            "locator": {"uri": "https://read-view.example/article#policy", "quote": "policy decision"},
            "assessment_method": "direct_source_review",
        }
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('evidence', %s, 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (evidence_id, stamp, stamp, json.dumps(evidence_payload), stamp),
        )
        connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('evidence', %s, 1, %s, 'fixture', 'initial', %s)",
            (evidence_id, stamp, json.dumps(evidence_payload)),
        )
        glossary_draft = {
            "schema_version": "1.0.0", "id": glossary_id,
            "lemma": "policy", "term_language": "en", "part_of_speech": "noun",
            "senses": [{
                "language": "en",
                "definition": "A course of action adopted by an authority.",
            }],
            "status": "draft", "created_at": stamp,
        }
        connection.execute(
            "INSERT INTO glossary_entries "
            "(id, lemma, lemma_key, term_language, part_of_speech, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (%s, 'policy', 'policy', 'en', 'noun', 'draft', 1, %s, %s, "
            "'fixture', 'initial', %s)",
            (glossary_id, stamp, stamp, json.dumps(glossary_draft)),
        )
        glossary_approved = {
            **glossary_draft, "status": "approved",
            "reviewed_at": "2026-11-23T00:02:00Z",
            "reviewed_by": "human:glossary-reviewer",
            "review_note": "Definition checked against usage",
        }
        connection.execute(
            "UPDATE glossary_entries SET status = 'approved', revision = 2, "
            "updated_at = '2026-11-23T00:02:00Z', last_actor = 'human:glossary-reviewer', "
            "last_reason = 'approved glossary entry', payload_json = %s WHERE id = %s",
            (json.dumps(glossary_approved), glossary_id),
        )
        learning_payload = {
            "id": learning_id, "story_id": story_id, "kind": "headline_summary",
            "language": "en", "variant_group_id": "variant:read-view",
            "status": "draft",
            "content": {
                "text": "Draft summary",
                "segments": [{
                    "text": "The central bank announced a policy decision.",
                    "claim_ids": [claim_id], "role": "fact",
                }],
            },
            "claim_ids": [claim_id], "created_at": stamp,
        }
        connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, variant_group_id, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (%s, %s, 'headline_summary', 'en', 'variant:read-view', "
            "'draft', 1, %s, %s, 'fixture', 'initial', %s)",
            (learning_id, story_id, stamp, stamp, json.dumps(learning_payload)),
        )
        question_payload = {
            "schema_version": "1.0.0",
            "id": "learning:question-valid",
            "story_id": story_id,
            "kind": "comprehension_question",
            "language": "en",
            "status": "draft",
            "content": {
                "question": "What did the central bank announce?",
                "answer": "A policy decision.",
                "explanation": "The source article reports the decision.",
                "answer_claim_ids": [claim_id],
                "answer_evidence": [{
                    "claim_id": claim_id, "evidence_id": evidence_id,
                    "evidence_revision": 1,
                }],
            },
            "claim_ids": [claim_id],
            "created_at": stamp,
        }
        for question in (question_payload,):
            connection.execute(
                "INSERT INTO learning_objects "
                "(id, story_id, kind, language, status, revision, created_at, updated_at, "
                "last_actor, last_reason, payload_json) "
                "VALUES (%s, %s, 'comprehension_question', 'en', 'draft', 1, %s, %s, "
                "'fixture', 'initial', %s)",
                (question["id"], story_id, stamp, stamp, json.dumps(question)),
            )
        mcq_payload = {
            "schema_version": "1.0.0",
            "id": "learning:mcq-valid",
            "story_id": story_id,
            "kind": "mcq_question",
            "language": "en",
            "status": "draft",
            "content": {
                "question": "Which action did the central bank announce?",
                "explanation": "The article reports a policy decision.",
                "answer_claim_ids": [claim_id],
                "answer_evidence": [{
                    "claim_id": claim_id, "evidence_id": evidence_id,
                    "evidence_revision": 1,
                }],
                "options": [
                    {"id": "a", "text": "A policy decision"},
                    {"id": "b", "text": "A weather warning"},
                ],
                "correct_option_id": "a",
                "distractor_rationales": [{
                    "option_id": "b",
                    "rationale": "The source does not report a weather warning.",
                    "basis_claim_ids": [claim_id],
                }],
            },
            "claim_ids": [claim_id],
            "created_at": stamp,
        }
        connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES (%s, %s, 'mcq_question', 'en', 'draft', 1, %s, %s, "
            "'fixture', 'initial', %s)",
            (mcq_payload["id"], story_id, stamp, stamp, json.dumps(mcq_payload)),
        )
        vocabulary_payload = {
            "schema_version": "1.0.0",
            "id": "learning:vocabulary",
            "story_id": story_id,
            "kind": "vocabulary",
            "language": "en",
            "status": "draft",
            "content": {
                "term": "policy", "term_language": "en",
                "meaning": "A course of action adopted by an authority.",
                "context": "The central bank announced a policy decision.",
                "example": "The new policy affects interest rates.",
                "part_of_speech": "noun",
                "glossary_entry_id": glossary_id,
                "source_evidence_id": evidence_id,
                "source_evidence_revision": 1,
            },
            "claim_ids": [claim_id],
            "created_at": stamp,
        }
        connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES (%s, %s, 'vocabulary', 'en', 'draft', 1, %s, %s, "
            "'fixture', 'initial', %s)",
            (vocabulary_payload["id"], story_id, stamp, stamp, json.dumps(vocabulary_payload)),
        )
        source_payload = {
            "id": source_id, "publisher_name": "Read View Publisher",
            "status": "active", "created_at": stamp, "updated_at": stamp,
        }
        connection.execute(
            "INSERT INTO sources "
            "(id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES (%s, %s, 'active', %s, %s, %s)",
            (source_id, source_payload["publisher_name"], stamp, stamp, json.dumps(source_payload)),
        )
        endpoint_payload = {
            "id": endpoint_id, "source_id": source_id,
            "endpoint_uri": "https://read-view.example/feed", "status": "active",
            "created_at": stamp, "updated_at": stamp,
        }
        connection.execute(
            "INSERT INTO source_endpoints "
            "(id, source_id, endpoint_uri, status, created_at, updated_at, payload_json) "
            "VALUES (%s, %s, %s, 'active', %s, %s, %s)",
            (
                endpoint_id, source_id, endpoint_payload["endpoint_uri"],
                stamp, stamp, json.dumps(endpoint_payload),
            ),
        )
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, "
            "languages_json, principal_kind) "
            "VALUES ('principal:read-view', 'Read View', %s, 1, %s, 'fixture', "
            "'initial', '[]', 'human')",
            (json.dumps(["edition_approver"]), stamp),
        )
        pipeline_payload = {
            "schema_version": "1.0.0", "id": pipeline_id, "status": "queued",
            "started_at": stamp, "software_version": "test-1",
            "config_hash": "sha256:" + "f" * 64,
            "input_hashes": [], "initiated_by": "integration-test",
        }
        connection.execute(
            "INSERT INTO pipeline_runs "
            "(id, status, revision, started_at, payload_json) "
            "VALUES (%s, 'queued', 1, %s, %s)",
            (pipeline_id, stamp, json.dumps(pipeline_payload)),
        )
        connection.execute(
            "INSERT INTO pipeline_run_revisions "
            "(pipeline_run_id, revision, created_at, actor, reason, payload_json) "
            "VALUES (%s, 1, %s, 'fixture', 'initial', %s)",
            (pipeline_id, stamp, json.dumps(pipeline_payload)),
        )
        connection.execute(
            """INSERT INTO pipeline_stage_checkpoints
            (pipeline_run_id, stage_ordinal, stage_name, input_fingerprint,
             idempotency_key, status, revision, created_at, updated_at,
             actor, reason, metadata_json)
            VALUES (%s, 0, 'collect', %s, %s, 'executing', 1, %s, %s,
                    'fixture', 'initial', '{}')""",
            (pipeline_id, "sha256:" + "a" * 64, "sha256:" + "b" * 64, stamp, stamp),
        )
        connection.execute(
            """INSERT INTO pipeline_worker_leases
            (pipeline_run_id, worker_id, token_hash, fencing_token, status,
             acquired_at, heartbeat_at, expires_at, revision, updated_at,
             actor, reason)
            VALUES (%s, 'worker:read-view', %s, 1, 'active', %s, %s,
                    '2026-11-23T00:10:00Z', 1, %s, 'fixture', 'initial')""",
            (pipeline_id, "sha256:" + "c" * 64, stamp, stamp, stamp),
        )

    with connect() as connection:
        view = PostgresEditionRegistryReadView(connection.cursor())
        assert view.get_domain_record("story", story_id).to_payload() == story_payload
        assert view.domain_record_history("story", story_id)[0]["revision"] == 1
        assert view.timeline_event_history(story_id) == []
        assert view.perspective_history(story_id) == []
        assert view.claim_relation_history(story_id) == []
        assert view.story_decision_history(story_id) == []
        learning = view.get_learning_object(learning_id)
        assert learning is not None and learning.revision == 1
        assert learning.to_payload() == learning_payload
        assert view.learning_object_history(learning_id)[0]["revision"] == 1
        assert view.get_source(source_id).to_payload() == source_payload
        assert view.get_endpoint(endpoint_id).to_payload() == endpoint_payload
        assert view.get_benchmark_principal("principal:read-view")["active"] is True
        connection.execute(
            "UPDATE sources SET status = 'inactive' WHERE id = %s", (source_id,)
        )
        with pytest.raises(
            PostgresEditionReadAdapterError,
            match="source indexed fields do not match payload",
        ):
            view.get_source(source_id)
        pipeline_read = view.get_pipeline_run(pipeline_id)
        assert pipeline_read["record"] == pipeline_payload
        assert pipeline_read["history"][0]["revision"] == 1
        checkpoints = view.get_pipeline_stage_checkpoints(pipeline_id)
        assert len(checkpoints) == 1
        assert checkpoints[0]["stage_name"] == "collect"
        assert checkpoints[0]["history"][0]["revision"] == 1
        lease = view.get_pipeline_worker_lease(pipeline_id)
        assert lease["worker_id"] == "worker:read-view"
        assert lease["fencing_token"] == 1
        assert lease["history"][0]["revision"] == 1
        from sngso_engine.registry import SQLiteRegistry
        sqlite_registry = SQLiteRegistry(":memory:")
        sqlite_registry._connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('story', ?, 1, ?, ?, ?, 'fixture', 'initial', ?)",
            (story_id, stamp, stamp, json.dumps(story_payload), stamp),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('story', ?, 1, ?, 'fixture', 'initial', ?)",
            (story_id, stamp, json.dumps(story_payload)),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('article', ?, 1, ?, ?, ?, 'fixture', 'initial', ?)",
            (article_id, stamp, stamp, json.dumps(article_payload), stamp),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('article', ?, 1, ?, 'fixture', 'initial', ?)",
            (article_id, stamp, json.dumps(article_payload)),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('claim', ?, 1, ?, ?, ?, 'fixture', 'initial', ?)",
            (claim_id, stamp, stamp, json.dumps(claim_payload), stamp),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('claim', ?, 1, ?, 'fixture', 'initial', ?)",
            (claim_id, stamp, json.dumps(claim_payload)),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('evidence', ?, 1, ?, ?, ?, 'fixture', 'initial', ?)",
            (evidence_id, stamp, stamp, json.dumps(evidence_payload), stamp),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO domain_record_revisions "
            "(record_type, record_id, revision, created_at, actor, reason, payload_json) "
            "VALUES ('evidence', ?, 1, ?, 'fixture', 'initial', ?)",
            (evidence_id, stamp, json.dumps(evidence_payload)),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO glossary_entries "
            "(id, lemma, lemma_key, term_language, part_of_speech, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (?, 'policy', 'policy', 'en', 'noun', 'draft', 1, ?, ?, "
            "'fixture', 'initial', ?)",
            (glossary_id, stamp, stamp, json.dumps(glossary_draft)),
        )
        sqlite_registry._connection.execute(
            "UPDATE glossary_entries SET status = 'approved', revision = 2, "
            "updated_at = '2026-11-23T00:02:00Z', last_actor = 'human:glossary-reviewer', "
            "last_reason = 'approved glossary entry', payload_json = ? WHERE id = ?",
            (json.dumps(glossary_approved), glossary_id),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, variant_group_id, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (?, ?, 'headline_summary', 'en', 'variant:read-view', "
            "'draft', 1, ?, ?, 'fixture', 'initial', ?)",
            (
                learning_id, story_id, stamp, stamp, json.dumps(learning_payload),
            ),
        )
        for question in (question_payload,):
            sqlite_registry._connection.execute(
                "INSERT INTO learning_objects "
                "(id, story_id, kind, language, status, revision, created_at, updated_at, "
                "last_actor, last_reason, payload_json) "
                "VALUES (?, ?, 'comprehension_question', 'en', 'draft', 1, ?, ?, "
                "'fixture', 'initial', ?)",
                (question["id"], story_id, stamp, stamp, json.dumps(question)),
            )
        sqlite_registry._connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES (?, ?, 'mcq_question', 'en', 'draft', 1, ?, ?, 'fixture', 'initial', ?)",
            (mcq_payload["id"], story_id, stamp, stamp, json.dumps(mcq_payload)),
        )
        sqlite_registry._connection.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES (?, ?, 'vocabulary', 'en', 'draft', 1, ?, ?, 'fixture', 'initial', ?)",
            (vocabulary_payload["id"], story_id, stamp, stamp, json.dumps(vocabulary_payload)),
        )
        sqlite_snapshot = sqlite_registry.story_editorial_snapshot(story_id)
        postgres_snapshot = view.story_editorial_snapshot(story_id)
        assert postgres_snapshot == sqlite_snapshot
        sqlite_quality = sqlite_registry.learning_object_quality_report(learning_id)
        postgres_quality = view.learning_object_quality_report(learning_id)
        assert postgres_quality == sqlite_quality
        sqlite_question_quality = sqlite_registry.learning_object_quality_report(question_payload["id"])
        postgres_question_quality = view.learning_object_quality_report(question_payload["id"])
        assert postgres_question_quality == sqlite_question_quality
        assert next(
            item for item in postgres_question_quality["checks"]
            if item["name"] == "answer_evidence"
        )["passed"] is True
        sqlite_mcq_quality = sqlite_registry.learning_object_quality_report(mcq_payload["id"])
        postgres_mcq_quality = view.learning_object_quality_report(mcq_payload["id"])
        assert postgres_mcq_quality == sqlite_mcq_quality
        assert next(
            item for item in postgres_mcq_quality["checks"]
            if item["name"] == "mcq_distractor_rationale_coverage"
        )["passed"] is True
        assert any(item["check"] == "mcq_human_distractor_review" for item in postgres_mcq_quality["warnings"])
        sqlite_vocabulary_quality = sqlite_registry.learning_object_quality_report(vocabulary_payload["id"])
        postgres_vocabulary_quality = view.learning_object_quality_report(vocabulary_payload["id"])
        assert postgres_vocabulary_quality == sqlite_vocabulary_quality
        assert next(
            item for item in postgres_vocabulary_quality["checks"]
            if item["name"] == "vocabulary_source_evidence"
        )["passed"] is True
        assert next(
            item for item in postgres_vocabulary_quality["checks"]
            if item["name"] == "controlled_glossary"
        )["passed"] is True
        sqlite_registry.close()


def test_postgres_staging_reverifier_requires_exact_snapshot_and_quality_ports():
    from sngso_engine.postgres_edition_verifier import (
        build_postgres_staging_export_reverifier,
    )

    reverifier = build_postgres_staging_export_reverifier(
        story_snapshot_loader=None,
        learning_object_quality_loader=None,
    )
    assert callable(reverifier)


def test_postgres_specialized_learning_evidence_validation_locks_current_claim_and_evidence_heads(postgres_schema):
    """An evidence head cannot change concurrently after a learning object validates it."""
    connect = postgres_schema
    stamp = "2026-11-23T00:00:00Z"
    with connect() as setup:
        setup.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('story', 'story-lock', 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (stamp, stamp, json.dumps({"id": "story-lock", "status": "active"}), stamp),
        )
        setup.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('claim', 'claim-lock', 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (
                stamp, stamp,
                json.dumps({
                    "id": "claim-lock", "story_id": "story-lock",
                    "evidence_ids": ["evidence-lock"],
                    "provenance": [{"source_id": "source-lock"}],
                }),
                stamp,
            ),
        )
        evidence_v1 = {
            "id": "evidence-lock", "claim_id": "claim-lock",
            "source_id": "source-lock", "relationship": "supports",
            "locator": {"uri": "https://example.test/lock#evidence"},
        }
        setup.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('evidence', 'evidence-lock', 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (stamp, stamp, json.dumps(evidence_v1), stamp),
        )

    learning_payload = {
        "id": "learning-lock", "story_id": "story-lock",
        "kind": "comprehension_question", "language": "en", "status": "draft",
        "created_at": stamp, "claim_ids": ["claim-lock"],
        "content": {
            "question": "What does the evidence support?",
            "answer_claim_ids": ["claim-lock"],
            "answer_evidence": [{
                "claim_id": "claim-lock", "evidence_id": "evidence-lock",
                "evidence_revision": 1,
            }],
        },
    }
    evidence_v2 = {**evidence_v1, "summary": "Revised evidence description."}
    with connect() as holder:
        # The BEFORE INSERT trigger must hold FOR SHARE locks until this
        # transaction commits, not merely inspect a statement-level snapshot.
        holder.execute(
            "INSERT INTO learning_objects "
            "(id, story_id, kind, language, status, revision, created_at, updated_at, "
            "last_actor, last_reason, payload_json) "
            "VALUES ('learning-lock', 'story-lock', 'comprehension_question', 'en', "
            "'draft', 1, %s, %s, 'fixture', 'evidence lock test', %s)",
            (stamp, stamp, json.dumps(learning_payload)),
        )
        with connect() as contender:
            contender.execute("SET lock_timeout = '150ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                contender.execute(
                    "UPDATE domain_records SET revision = 2, updated_at = %s, "
                    "payload_json = %s, last_actor = 'concurrent-editor', "
                    "last_reason = 'concurrent evidence revision', last_revision_at = %s "
                    "WHERE record_type = 'evidence' AND id = 'evidence-lock'",
                    (stamp, json.dumps(evidence_v2), stamp),
                )
            contender.rollback()

    with connect() as check:
        assert check.execute(
            "SELECT revision FROM domain_records "
            "WHERE record_type = 'evidence' AND id = 'evidence-lock'"
        ).fetchone() == (1,)


def test_postgres_benchmark_review_quality_report_rechecks_artifact_and_draft_state(postgres_schema):
    from sngso_engine.postgres_edition_read import PostgresEditionRegistryReadView

    connect = postgres_schema
    artifact = {"artifact_id": "benchmark-quality", "items": [{"id": "item-1"}]}
    fingerprint = sha256_fingerprint(artifact)
    stamp = "2026-11-23T00:00:00Z"
    review = {
        "review_id": "review-quality",
        "benchmark_id": "benchmark-quality",
        "benchmark_fingerprint": fingerprint,
        "language": "en",
        "status": "draft",
        "reviewed_at": stamp,
        "reviewer": {
            "reviewer_id": "reviewer-quality",
            "role": "benchmark_reviewer",
            "language": "en",
            "qualification_basis": "Documented editorial and subject-matter experience.",
        },
        "source_materials": [],
        "corrections": [],
        "adjudication_note": "",
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES (%s, 'en', 'canonical-json-v1', %s, %s, %s, "
            "'fixture', 'quality report test')",
            ("benchmark-quality", fingerprint, json.dumps(artifact), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (%s, %s, %s, 'en', 'draft', 1, %s, %s, 'fixture', "
            "'quality report test', %s)",
            ("review-quality", "benchmark-quality", fingerprint, stamp, stamp, json.dumps(review)),
        )
        view = PostgresEditionRegistryReadView(connection.cursor())
        report = view.benchmark_review_quality_report("review-quality")
        assert report["revision"] == 1
        assert report["status"] == "draft"
        assert report["eligible_for_current_use"] is False
        checks = {item["name"]: item["passed"] for item in report["checks"]}
        assert checks == {
            "approved_review": False,
            "review_metadata": True,
            "benchmark_artifact": True,
            "current_source_permission": True,
            "adjudication_policy": True,
        }


def test_postgres_benchmark_quality_reports_fail_closed_for_unknown_records(postgres_schema):
    from sngso_engine.postgres_benchmark_quality import (
        PostgresBenchmarkQualityError,
        benchmark_acceptance_review_quality_report,
        benchmark_review_quality_report,
    )

    connect = postgres_schema
    with connect() as connection:
        with pytest.raises(PostgresBenchmarkQualityError, match="unknown benchmark review"):
            benchmark_review_quality_report(connection.cursor(), "missing-review")
        with pytest.raises(PostgresBenchmarkQualityError, match="unknown benchmark acceptance review"):
            benchmark_acceptance_review_quality_report(connection.cursor(), "missing-acceptance")


def test_postgres_story_snapshot_includes_benchmark_review_quality_and_history(postgres_schema):
    from sngso_engine.postgres_story_snapshot import load_postgres_story_editorial_snapshot

    connect = postgres_schema
    stamp = "2026-11-23T00:00:00Z"
    artifact = {"artifact_id": "benchmark-story", "items": [{"id": "item-story"}]}
    fingerprint = sha256_fingerprint(artifact)
    review = {
        "review_id": "review-story",
        "benchmark_id": "benchmark-story",
        "benchmark_fingerprint": fingerprint,
        "language": "en",
        "status": "draft",
        "reviewed_at": stamp,
        "reviewer": {
            "reviewer_id": "reviewer-story",
            "role": "benchmark_reviewer",
            "language": "en",
            "qualification_basis": "Documented editorial and subject-matter experience.",
        },
        "source_materials": [{
            "article_id": "article-story-benchmark",
            "source_id": "source-story-benchmark",
            "approved_use_reference": "permission-story-benchmark",
            "permission_grant_id": "grant-story-benchmark",
            "endpoint_id": "endpoint-story-benchmark",
            "permission_operation": "excerpt_storage",
            "canonical_uri": "https://example.test/story-benchmark",
            "content_fingerprint": "sha256:" + "b" * 64,
            "checked_at": stamp,
        }],
        "corrections": [],
        "adjudication_note": "",
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES ('source-story-benchmark', 'Publisher', 'active', %s, %s, %s)",
            (stamp, stamp, json.dumps({"id": "source-story-benchmark", "status": "active"})),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('story', 'story-benchmark', 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (stamp, stamp, json.dumps({
                "id": "story-benchmark", "status": "active",
                "article_ids": ["article-story-benchmark"],
            }), stamp),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('article', 'article-story-benchmark', 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (stamp, stamp, json.dumps({
                "id": "article-story-benchmark",
                "source_id": "source-story-benchmark",
                "canonical_uri": "https://example.test/story-benchmark",
                "content_fingerprint": "sha256:" + "b" * 64,
                "title": "Benchmark source article",
            }), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES ('benchmark-story', 'en', 'canonical-json-v1', "
            "%s, %s, %s, 'fixture', 'story snapshot test')",
            (fingerprint, json.dumps(artifact), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES ('review-story', 'benchmark-story', %s, 'en', 'draft', 1, %s, %s, "
            "'fixture', 'story snapshot test', %s)",
            (fingerprint, stamp, stamp, json.dumps(review)),
        )
        snapshot = load_postgres_story_editorial_snapshot(connection.cursor(), "story-benchmark")
        assert len(snapshot["benchmark_reviews"]) == 1
        benchmark_review = snapshot["benchmark_reviews"][0]
        assert benchmark_review["review_id"] == "review-story"
        assert benchmark_review["revision"] == 1
        assert benchmark_review["quality_report"]["eligible_for_current_use"] is False
        assert benchmark_review["history"][0]["revision"] == 1
        assert snapshot["benchmark_acceptance_reviews"] == []
        assert snapshot["fingerprint"] == sha256_fingerprint({
            key: value for key, value in snapshot.items() if key != "fingerprint"
        })


def test_postgres_benchmark_readiness_and_agreement_reports_are_descriptive_and_fail_closed(postgres_schema):
    from sngso_engine.postgres_edition_read import PostgresEditionRegistryReadView
    from sngso_engine.postgres_benchmark_quality import PostgresBenchmarkQualityError

    connect = postgres_schema
    artifact = {"artifact_id": "benchmark-readiness", "items": [{"id": "item-1"}]}
    fingerprint = sha256_fingerprint(artifact)
    stamp = "2026-11-23T00:00:00Z"
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES ('benchmark-readiness', 'en', "
            "'canonical-json-v1', %s, %s, %s, 'fixture', 'readiness report test')",
            (fingerprint, json.dumps(artifact), stamp),
        )
        view = PostgresEditionRegistryReadView(connection.cursor())
        report = view.benchmark_readiness_report("benchmark-readiness")
        assert report["status"] == "human_review_pending"
        assert report["artifact"]["fingerprint_verified"] is True
        assert report["eligible_approved_review_count"] == 0
        assert report["production_ready"] is False
        with pytest.raises(PostgresBenchmarkQualityError, match="no persisted score sheets"):
            view.benchmark_evaluator_agreement_report("benchmark-readiness", "item-1")


def test_postgres_approved_benchmark_review_rechecks_live_source_permission(postgres_schema):
    from sngso_engine.postgres_edition_read import PostgresEditionRegistryReadView

    connect = postgres_schema
    stamp = "2026-11-23T00:00:00Z"
    source_id, endpoint_id = "source:benchmark-rights", "endpoint:benchmark-rights"
    evidence_id, grant_id = "evidence:benchmark-rights", "grant:benchmark-rights"
    article_id, artifact_id = "article:benchmark-rights", "benchmark:approved-rights"
    fingerprint = "sha256:" + "a" * 64
    artifact = {"artifact_id": artifact_id, "items": [{"id": "item-1"}]}
    artifact_fingerprint = sha256_fingerprint(artifact)
    operations = {
        name: {"allowed": name == "excerpt_storage",
               "status": "approved" if name == "excerpt_storage" else "denied",
               "conditions": [],
               **({"evidence_ids": [evidence_id]} if name == "excerpt_storage" else {})}
        for name in (
            "discovery", "metadata", "excerpt_storage", "full_text_storage",
            "translation", "image_storage", "republication",
        )
    }
    grant = {
        "schema_version": "1.0.0", "id": grant_id, "source_id": source_id,
        "endpoint_ids": [endpoint_id], "status": "approved", "operations": operations,
        "evidence_ids": [evidence_id], "basis": "Publisher-approved excerpt storage.",
        "effective_from": "2026-01-01T00:00:00Z", "effective_until": "2027-01-01T00:00:00Z",
        "created_at": stamp, "reviewed_at": stamp, "reviewed_by": "human:rights-reviewer",
    }
    review = {
        "review_id": "review:approved-rights", "benchmark_id": artifact_id,
        "benchmark_fingerprint": artifact_fingerprint, "language": "en", "status": "approved",
        "reviewed_at": stamp,
        "reviewer": {
            "reviewer_id": "human:benchmark-reviewer", "role": "benchmark_reviewer",
            "language": "en", "qualification_basis": "Documented editorial and subject-matter experience.",
        },
        "source_materials": [{
            "article_id": article_id, "source_id": source_id,
            "approved_use_reference": evidence_id, "permission_grant_id": grant_id,
            "endpoint_id": endpoint_id, "permission_operation": "excerpt_storage",
            "canonical_uri": "https://rights.example/benchmark-article",
            "content_fingerprint": fingerprint, "checked_at": stamp,
        }],
        "corrections": [], "adjudication_note": "Source rights and benchmark metadata reviewed.",
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES (%s, 'Rights Publisher', 'active', %s, %s, %s)",
            (source_id, stamp, stamp, json.dumps({
                "id": source_id, "publisher_name": "Rights Publisher", "status": "active",
                "created_at": stamp, "updated_at": stamp,
            })),
        )
        connection.execute(
            "INSERT INTO source_endpoints "
            "(id, source_id, endpoint_uri, status, created_at, updated_at, payload_json) "
            "VALUES (%s, %s, 'https://rights.example/feed', 'active', %s, %s, %s)",
            (endpoint_id, source_id, stamp, stamp, json.dumps({
                "id": endpoint_id, "source_id": source_id,
                "endpoint_uri": "https://rights.example/feed", "status": "active",
                "created_at": stamp, "updated_at": stamp,
            })),
        )
        connection.execute(
            "INSERT INTO permission_evidence "
            "(id, source_id, review_status, content_fingerprint, captured_at, payload_json) "
            "VALUES (%s, %s, 'verified', %s, %s, %s)",
            (evidence_id, source_id, fingerprint, stamp, json.dumps({
                "id": evidence_id, "source_id": source_id, "review_status": "verified",
                "content_fingerprint": fingerprint, "captured_at": stamp,
            })),
        )
        connection.execute(
            "INSERT INTO permission_grants "
            "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
            "VALUES (%s, %s, 'approved', %s, %s, 1, %s)",
            (grant_id, source_id, grant["effective_from"], grant["effective_until"], json.dumps(grant)),
        )
        connection.execute(
            "INSERT INTO grant_endpoints (grant_id, source_id, endpoint_id) VALUES (%s, %s, %s)",
            (grant_id, source_id, endpoint_id),
        )
        connection.execute(
            "INSERT INTO grant_evidence (grant_id, source_id, evidence_id) VALUES (%s, %s, %s)",
            (grant_id, source_id, evidence_id),
        )
        connection.execute(
            "INSERT INTO grant_operation_evidence "
            "(grant_id, source_id, operation, evidence_id) VALUES (%s, %s, 'excerpt_storage', %s)",
            (grant_id, source_id, evidence_id),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('article', %s, 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (article_id, stamp, stamp, json.dumps({
                "id": article_id, "source_id": source_id,
                "canonical_uri": "https://rights.example/benchmark-article",
                "content_fingerprint": fingerprint,
            }), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES (%s, 'en', 'canonical-json-v1', %s, %s, %s, "
            "'fixture', 'approved review test')",
            (artifact_id, artifact_fingerprint, json.dumps(artifact), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (%s, %s, %s, 'en', 'approved', 1, %s, %s, 'fixture', "
            "'approved review test', %s)",
            (review["review_id"], artifact_id, artifact_fingerprint, stamp, stamp, json.dumps(review)),
        )
    with connect() as connection:
        report = PostgresEditionRegistryReadView(connection.cursor()).benchmark_review_quality_report(
            review["review_id"]
        )
        assert report["eligible_for_current_use"] is True
        checks = {item["name"]: item["passed"] for item in report["checks"]}
        assert checks["current_source_permission"] is True
        with connect() as contender:
            contender.execute("SET lock_timeout = '150ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                contender.execute(
                    "UPDATE permission_evidence SET review_status = 'pending' WHERE id = %s",
                    (evidence_id,),
                )
            contender.rollback()

    with connect() as connection:
        connection.execute(
            "UPDATE permission_evidence SET review_status = 'pending' WHERE id = %s",
            (evidence_id,),
        )
    with connect() as connection:
        report = PostgresEditionRegistryReadView(connection.cursor()).benchmark_review_quality_report(
            review["review_id"]
        )
        assert report["eligible_for_current_use"] is False
        checks = {item["name"]: item["passed"] for item in report["checks"]}
        assert checks["current_source_permission"] is False


def test_postgres_acceptance_quality_rechecks_exact_protocol_execution_and_review_coverage(postgres_schema):
    from datetime import datetime, timezone
    from sngso_engine.benchmark_study import validate_benchmark_study_protocol
    from sngso_engine.benchmark_study_execution import validate_benchmark_study_execution
    from sngso_engine.postgres_edition_read import PostgresEditionRegistryReadView
    from test_benchmark_study import _protocol
    from test_benchmark_study_execution import _execution
    from test_benchmark_acceptance_review import _review

    connect = postgres_schema
    stamp = "2026-11-23T00:00:00Z"
    artifact_id = "artifact:bn:headlines"
    artifact = {"artifact_id": artifact_id, "items": [{"id": "blind:1"}, {"id": "blind:2"}]}
    artifact_fp = sha256_fingerprint(artifact)
    protocol = _protocol()
    protocol["benchmark_fingerprint"] = artifact_fp
    protocol_report = validate_benchmark_study_protocol(protocol)
    protocol_fp = protocol_report["protocol_fingerprint"]
    protocol_id = "protocol-record:acceptance-test"
    execution = _execution()
    execution.update({
        "protocol_fingerprint": protocol_fp,
        "benchmark_fingerprint": artifact_fp,
    })
    execution_report = validate_benchmark_study_execution(execution)
    execution_fp = execution_report["execution_fingerprint"]
    execution_id = execution["execution_id"]
    review = _review()
    review.update({
        "study_id": protocol["study_id"],
        "study_version": protocol["study_version"],
        "protocol_fingerprint": protocol_fp,
        "execution_id": execution_id,
        "execution_fingerprint": execution_fp,
        "benchmark_id": artifact_id,
        "benchmark_fingerprint": artifact_fp,
        "rubric_version": protocol["rubric_version"],
        "reviewer_id": "acceptance:quality-test",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    acceptance_fp = sha256_fingerprint(review)
    with connect() as connection:
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES (%s, 'bn', 'canonical-json-v1', %s, %s, %s, "
            "'fixture', 'acceptance quality test')",
            (artifact_id, artifact_fp, json.dumps(artifact), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, "
            "languages_json, principal_kind) VALUES "
            "('methodologist:one', 'Methodologist', '[]', 1, %s, 'fixture', 'test', '[\"bn\"]', 'human'), "
            "('acceptance:quality-test', 'Acceptance Reviewer', '[\"benchmark_acceptance_reviewer\"]', "
            "1, %s, 'fixture', 'test', '[\"bn\"]', 'human')",
            (stamp, stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_study_protocols "
            "(protocol_record_id, study_id, study_version, benchmark_id, benchmark_fingerprint, "
            "rubric_version, protocol_fingerprint, status, payload_json, created_at, actor, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'protocol_only', %s, %s, 'fixture', 'test')",
            (protocol_id, protocol["study_id"], protocol["study_version"], artifact_id,
             artifact_fp, protocol["rubric_version"], protocol_fp, json.dumps(protocol), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_study_executions "
            "(execution_id, protocol_record_id, study_id, study_version, protocol_fingerprint, "
            "benchmark_id, benchmark_fingerprint, rubric_version, execution_fingerprint, status, "
            "methodologist_id, issuer, subject, provider_key_id, provider_adapter, authenticated_at, "
            "payload_json, created_at, actor, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'analysis_reviewed', "
            "'methodologist:one', 'https://login.example.test/', 'oidc:methodologist:one', "
            "'test-key', 'test-adapter/1', %s, %s, %s, 'fixture', 'test')",
            (execution_id, protocol_id, execution["study_id"], execution["study_version"],
             protocol_fp, artifact_id, artifact_fp, execution["rubric_version"], execution_fp,
             stamp, json.dumps(execution), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_acceptance_reviews "
            "(review_id, execution_id, protocol_record_id, study_id, study_version, "
            "protocol_fingerprint, execution_fingerprint, acceptance_fingerprint, benchmark_id, "
            "benchmark_fingerprint, rubric_version, reviewer_id, reviewer_role, decision, issuer, "
            "subject, provider_key_id, provider_adapter, authenticated_at, payload_json, created_at, "
            "actor, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "'independent_acceptance_reviewer', %s, 'https://login.example.test/', "
            "'oidc:acceptance:quality-test', 'test-key', 'test-adapter/1', %s, %s, %s, "
            "'fixture', 'test')",
            (review["review_id"], execution_id, protocol_id, review["study_id"],
             review["study_version"], protocol_fp, execution_fp, acceptance_fp, artifact_id,
             artifact_fp, review["rubric_version"], review["reviewer_id"], review["decision"],
             stamp, json.dumps(review), stamp),
        )
    with connect() as connection:
        report = PostgresEditionRegistryReadView(connection.cursor()).benchmark_acceptance_review_quality_report(
            review["review_id"]
        )
        checks = {item["name"]: item["passed"] for item in report["checks"]}
        assert checks["acceptance_fingerprint"] is True
        assert checks["registered_protocol"] is True
        assert checks["registered_execution"] is True
        assert checks["benchmark_artifact"] is True
        assert checks["acceptance_contract"] is True
        assert checks["accepted_decision"] is True
        assert checks["current_human_source_reviews"] is False

        assert report["eligible_for_scoped_reference"] is False
        assert report["production_ready"] is False
        assert report["authorization_granted"] is False

        source_id = "source:acceptance-review-coverage"
        endpoint_id = "endpoint:acceptance-review-coverage"
        evidence_id = "evidence:acceptance-review-coverage"
        grant_id = "grant:acceptance-review-coverage"
        article_id = "article:acceptance-review-coverage"
        source_fingerprint = "sha256:" + "d" * 64
        source_material = {
            "article_id": article_id, "source_id": source_id,
            "approved_use_reference": evidence_id, "permission_grant_id": grant_id,
            "endpoint_id": endpoint_id, "permission_operation": "excerpt_storage",
            "canonical_uri": "https://acceptance.example/article",
            "content_fingerprint": source_fingerprint, "checked_at": stamp,
        }
        operation_records = {
            name: {
                "allowed": name == "excerpt_storage",
                "status": "approved" if name == "excerpt_storage" else "denied",
                "conditions": [],
                **({"evidence_ids": [evidence_id]} if name == "excerpt_storage" else {}),
            }
            for name in (
                "discovery", "metadata", "excerpt_storage", "full_text_storage",
                "translation", "image_storage", "republication",
            )
        }
        source_grant = {
            "schema_version": "1.0.0", "id": grant_id, "source_id": source_id,
            "endpoint_ids": [endpoint_id], "status": "approved",
            "operations": operation_records, "evidence_ids": [evidence_id],
            "basis": "Publisher-approved excerpt storage for benchmark review.",
            "effective_from": "2026-01-01T00:00:00Z",
            "effective_until": "2027-01-01T00:00:00Z",
            "created_at": stamp, "reviewed_at": stamp,
            "reviewed_by": "human:rights-reviewer",
        }
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES (%s, 'Acceptance Publisher', 'active', %s, %s, %s)",
            (source_id, stamp, stamp, json.dumps({
                "id": source_id, "publisher_name": "Acceptance Publisher",
                "status": "active", "created_at": stamp, "updated_at": stamp,
            })),
        )
        connection.execute(
            "INSERT INTO source_endpoints "
            "(id, source_id, endpoint_uri, status, created_at, updated_at, payload_json) "
            "VALUES (%s, %s, 'https://acceptance.example/feed', 'active', %s, %s, %s)",
            (endpoint_id, source_id, stamp, stamp, json.dumps({
                "id": endpoint_id, "source_id": source_id,
                "endpoint_uri": "https://acceptance.example/feed", "status": "active",
                "created_at": stamp, "updated_at": stamp,
            })),
        )
        connection.execute(
            "INSERT INTO permission_evidence "
            "(id, source_id, review_status, content_fingerprint, captured_at, payload_json) "
            "VALUES (%s, %s, 'verified', %s, %s, %s)",
            (evidence_id, source_id, source_fingerprint, stamp, json.dumps({
                "id": evidence_id, "source_id": source_id, "review_status": "verified",
                "content_fingerprint": source_fingerprint, "captured_at": stamp,
            })),
        )
        connection.execute(
            "INSERT INTO permission_grants "
            "(id, source_id, status, effective_from, effective_until, revision, payload_json) "
            "VALUES (%s, %s, 'approved', %s, %s, 1, %s)",
            (grant_id, source_id, source_grant["effective_from"],
             source_grant["effective_until"], json.dumps(source_grant)),
        )
        connection.execute(
            "INSERT INTO grant_endpoints (grant_id, source_id, endpoint_id) VALUES (%s, %s, %s)",
            (grant_id, source_id, endpoint_id),
        )
        connection.execute(
            "INSERT INTO grant_evidence (grant_id, source_id, evidence_id) VALUES (%s, %s, %s)",
            (grant_id, source_id, evidence_id),
        )
        connection.execute(
            "INSERT INTO grant_operation_evidence "
            "(grant_id, source_id, operation, evidence_id) "
            "VALUES (%s, %s, 'excerpt_storage', %s)",
            (grant_id, source_id, evidence_id),
        )
        connection.execute(
            "INSERT INTO domain_records "
            "(record_type, id, revision, created_at, updated_at, payload_json, "
            "last_actor, last_reason, last_revision_at) "
            "VALUES ('article', %s, 1, %s, %s, %s, 'fixture', 'initial', %s)",
            (article_id, stamp, stamp, json.dumps({
                "id": article_id, "source_id": source_id,
                "canonical_uri": source_material["canonical_uri"],
                "content_fingerprint": source_fingerprint,
            }), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_principals "
            "(principal_id, display_name, roles_json, active, created_at, created_by, reason, "
            "languages_json, principal_kind) VALUES "
            "('human:one', 'Human One', '[\"benchmark_reviewer\"]', 1, %s, 'fixture', "
            "'reviewer', '[\"bn\"]', 'human'), "
            "('human:two', 'Human Two', '[\"benchmark_reviewer\"]', 1, %s, 'fixture', "
            "'reviewer', '[\"bn\"]', 'human')",
            (stamp, stamp),
        )
        for reviewer_id in ("human:one", "human:two"):
            source_review = {
                "review_id": "review:" + reviewer_id,
                "benchmark_id": artifact_id, "benchmark_fingerprint": artifact_fp,
                "language": "bn", "status": "approved", "reviewed_at": stamp,
                "reviewer": {
                    "reviewer_id": reviewer_id, "role": "benchmark_reviewer",
                    "language": "bn",
                    "qualification_basis": "Documented editorial and subject-matter experience.",
                },
                "source_materials": [source_material], "corrections": [],
                "adjudication_note": "Reviewed source evidence and benchmark item.",
            }
            connection.execute(
                "INSERT INTO benchmark_reviews "
                "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
                "created_at, updated_at, last_actor, last_reason, payload_json) "
                "VALUES (%s, %s, %s, 'bn', 'approved', 1, %s, %s, %s, "
                "'source review', %s)",
                (source_review["review_id"], artifact_id, artifact_fp, stamp, stamp,
                 reviewer_id, json.dumps(source_review)),
            )
            attestation = {
                "attestation_id": "attestation:" + reviewer_id,
                "review_id": source_review["review_id"], "review_revision": 1,
                "principal_id": reviewer_id, "issuer": "https://login.example.test/",
                "audience": "sngso-benchmark-review", "subject": "oidc:" + reviewer_id,
                "authenticated_at": "2026-11-23T00:01:00Z",
                "expires_at": "2026-11-23T01:00:00Z",
                "authentication_methods": ["password", "webauthn"],
                "authentication_context": "interactive-mfa",
                "provider_key_id": "test-key", "provider_adapter": "test-adapter/1",
                "session_fingerprint": "sha256:" + ("e" if reviewer_id == "human:one" else "f") * 64,
                "attested_at": "2026-11-23T00:02:00Z",
            }
            connection.execute(
                "INSERT INTO benchmark_review_identity_attestations "
                "(attestation_id, review_id, review_revision, principal_id, issuer, audience, "
                "subject, authenticated_at, expires_at, authentication_methods_json, "
                "authentication_context, provider_key_id, provider_adapter, session_fingerprint, "
                "attested_at, payload_json, actor, reason) "
                "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (attestation["attestation_id"], attestation["review_id"], reviewer_id,
                 attestation["issuer"], attestation["audience"], attestation["subject"],
                 attestation["authenticated_at"], attestation["expires_at"],
                 json.dumps(attestation["authentication_methods"]),
                 attestation["authentication_context"], attestation["provider_key_id"],
                 attestation["provider_adapter"], attestation["session_fingerprint"],
                 attestation["attested_at"], json.dumps(attestation), reviewer_id,
                 "test attestation"),
            )

    with connect() as connection:
        report = PostgresEditionRegistryReadView(connection.cursor()).benchmark_acceptance_review_quality_report(
            review["review_id"]
        )
        checks = {item["name"]: item["passed"] for item in report["checks"]}
        assert checks["current_human_source_reviews"] is True
        assert len([item for item in report["source_reviews"] if item["eligible_current_source_review"]]) == 2
        assert report["eligible_for_scoped_reference"] is True
        assert report["production_ready"] is False
        assert report["authorization_granted"] is False


def test_postgres_benchmark_story_snapshot_matches_sqlite_canonical_fingerprint(postgres_schema):
    from sngso_engine.registry import SQLiteRegistry
    from sngso_engine.postgres_story_snapshot import load_postgres_story_editorial_snapshot

    connect = postgres_schema
    stamp = "2026-11-23T00:00:00Z"
    source_id = "source:benchmark-parity"
    article_id = "article:benchmark-parity"
    story_id = "story:benchmark-parity"
    artifact_id = "benchmark:story-parity"
    artifact = {"artifact_id": artifact_id, "items": [{"id": "item-parity"}]}
    artifact_fp = sha256_fingerprint(artifact)
    source = {
        "schema_version": "1.0.0", "id": source_id,
        "publisher_name": "Parity Publisher", "publisher_type": "newspaper",
        "canonical_homepage": "https://parity.example/",
        "domains": ["parity.example"], "status": "active",
        "created_at": stamp, "updated_at": stamp,
    }
    article = {
        "schema_version": "1.0.0", "id": article_id, "source_id": source_id,
        "canonical_uri": "https://parity.example/article", "title": "Parity article",
        "source_language": "en", "discovered_at": stamp, "record_status": "active",
    }
    story = {
        "schema_version": "1.0.0", "id": story_id, "canonical_title": "Parity story",
        "status": "developing", "created_at": stamp, "updated_at": stamp,
        "article_ids": [article_id],
    }
    review = {
        "review_id": "review:benchmark-parity", "benchmark_id": artifact_id,
        "benchmark_fingerprint": artifact_fp, "language": "en", "status": "draft",
        "reviewed_at": stamp,
        "reviewer": {
            "reviewer_id": "reviewer:parity", "role": "benchmark_reviewer",
            "language": "en", "qualification_basis": "Documented editorial and subject-matter experience.",
        },
        "source_materials": [{
            "article_id": article_id, "source_id": source_id,
            "approved_use_reference": "evidence:parity",
            "permission_grant_id": "grant:parity", "endpoint_id": "endpoint:parity",
            "permission_operation": "excerpt_storage",
            "canonical_uri": article["canonical_uri"],
            "content_fingerprint": "sha256:" + "c" * 64, "checked_at": stamp,
        }],
        "corrections": [], "adjudication_note": "",
    }

    with SQLiteRegistry() as sqlite_registry:
        sqlite_registry.add_source(source)
        sqlite_registry.add_domain_record("article", article)
        sqlite_registry.add_domain_record("story", story)
        sqlite_conn = sqlite_registry._connection
        sqlite_conn.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES (?, 'en', 'canonical-json-v1', ?, ?, ?, "
            "'fixture', 'snapshot parity')",
            (artifact_id, artifact_fp, json.dumps(artifact), stamp),
        )
        sqlite_conn.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (?, ?, ?, 'en', 'draft', 1, ?, ?, 'fixture', 'snapshot parity', ?)",
            (review["review_id"], artifact_id, artifact_fp, stamp, stamp, json.dumps(review)),
        )
        sqlite_snapshot = sqlite_registry.story_editorial_snapshot(story_id)

        source_row = sqlite_conn.execute(
            "SELECT id, publisher_name, status, created_at, updated_at, payload_json "
            "FROM sources WHERE id = ?", (source_id,),
        ).fetchone()
        domain_rows = sqlite_conn.execute(
            """SELECT record_type, id, revision, created_at, updated_at, payload_json,
                      last_actor, last_reason, last_revision_at
               FROM domain_records WHERE (record_type, id) IN (('article', ?), ('story', ?))
               ORDER BY record_type, id""",
            (article_id, story_id),
        ).fetchall()

    with connect() as connection:
        connection.execute(
            "INSERT INTO sources (id, publisher_name, status, created_at, updated_at, payload_json) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            tuple(source_row),
        )
        for row in domain_rows:
            connection.execute(
                "INSERT INTO domain_records "
                "(record_type, id, revision, created_at, updated_at, payload_json, "
                "last_actor, last_reason, last_revision_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                tuple(row),
            )
        connection.execute(
            "INSERT INTO benchmark_artifacts "
            "(artifact_id, language, canonicalization, content_fingerprint, content_json, "
            "created_at, actor, reason) VALUES (%s, 'en', 'canonical-json-v1', %s, %s, %s, "
            "'fixture', 'snapshot parity')",
            (artifact_id, artifact_fp, json.dumps(artifact), stamp),
        )
        connection.execute(
            "INSERT INTO benchmark_reviews "
            "(review_id, benchmark_id, benchmark_fingerprint, language, status, revision, "
            "created_at, updated_at, last_actor, last_reason, payload_json) "
            "VALUES (%s, %s, %s, 'en', 'draft', 1, %s, %s, 'fixture', 'snapshot parity', %s)",
            (review["review_id"], artifact_id, artifact_fp, stamp, stamp, json.dumps(review)),
        )
        postgres_snapshot = load_postgres_story_editorial_snapshot(connection.cursor(), story_id)

    assert postgres_snapshot == sqlite_snapshot


def test_postgres_staging_runtime_role_has_only_atomic_staging_and_audit_write_privileges(postgres_schema):
    from sngso_engine.postgres_edition_staging import stage_edition_export

    connect = postgres_schema
    role_name = "sngso_stage_runtime_" + uuid.uuid4().hex[:12]
    schema_name = None
    try:
        with connect() as connection:
            identity, now, package = _seed_atomic_staging_case(connection)
            schema_name = connection.execute("SELECT current_schema()").fetchone()[0]
            connection.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name))
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            connection.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            connection.execute(
                sql.SQL("GRANT INSERT ON TABLE {}.edition_staging_events, {}.audit_events TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            connection.execute(
                sql.SQL("GRANT UPDATE ON TABLE {}.edition_staging_channel_state, {}.audit_chain_head TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            artifact_schema = connection.execute(
                "SELECT table_schema FROM information_schema.tables "
                "WHERE table_name = 'edition_artifacts'"
            ).fetchone()[0]
            assert artifact_schema == schema_name
            assert connection.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT')",
                (role_name, f"{schema_name}.edition_artifacts"),
            ).fetchone()[0] is True

        with connect() as writer:
            writer.autocommit = True
            writer.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_name)))
            writer.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name))
            )
            result = stage_edition_export(
                writer,
                manifest_id="manifest:atomic",
                operation="promote",
                identity=identity,
                reason="least-privilege staging role test",
                expected_state_revision=0,
                reverify_export=lambda cursor, artifact, evaluated_at: package,
                event_id="least-privilege-stage-event",
                now=now,
            )
            assert result["state"]["state_revision"] == 1
            assert result["audit"]["sequence"] == 1

        with connect() as restricted:
            restricted.autocommit = True
            restricted.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_name)))
            restricted.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name))
            )
            denied = (
                "UPDATE audit_events SET event_type = 'tampered' WHERE false",
                "DELETE FROM audit_events WHERE false",
                "TRUNCATE audit_events",
                "UPDATE edition_staging_events SET reason = 'tampered' WHERE false",
                "DELETE FROM edition_staging_events WHERE false",
                "TRUNCATE edition_staging_events",
                "DELETE FROM edition_staging_channel_state WHERE false",
                "TRUNCATE edition_staging_channel_state",
                "UPDATE sources SET status = 'inactive' WHERE false",
            )
            for statement in denied:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    restricted.execute(statement)
    finally:
        if schema_name is not None:
            with psycopg.connect(os.environ["TEST_POSTGRES_DSN"], autocommit=True) as admin:
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))
