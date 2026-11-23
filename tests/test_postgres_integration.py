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
            assert apply_postgres_migrations(connection) == (1, 2, 3, 4, 5, 6, 7, 8, 9)
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
        assert rows == [(1, "0001_audit_chain.sql"), (2, "0002_audit_head_guards.sql"), (3, "0003_source_permission_domain_registry.sql"), (4, "0004_collection_ingestion.sql"), (5, "0005_story_timeline.sql"), (6, "0006_perspectives_claim_relations.sql"), (7, "0007_learning_glossary_revisions.sql"), (8, "0008_benchmark_review_core.sql"), (9, "0009_benchmark_assignments_identity_scores.sql")]



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
            (json.dumps({"revision": 1}),),
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

        accepted_payload = {**assignment_payload, "status": "accepted"}
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
