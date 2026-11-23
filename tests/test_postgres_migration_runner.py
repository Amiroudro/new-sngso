from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sngso_engine.postgres_migration_runner import (
    PostgresMigrationError,
    apply_postgres_migrations,
)


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None, **kwargs):
        self.statements.append((sql, params, kwargs))
        if sql.lstrip().startswith("INSERT INTO postgres_schema_migrations"):
            self.rows.append(params)

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows=()):
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="IDLE")
        )
        self.db_cursor = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        self.info.transaction_status.name = "INTRANS"
        try:
            yield
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
        finally:
            self.info.transaction_status.name = "IDLE"

    def cursor(self):
        return self.db_cursor


def test_postgres_migration_runner_applies_and_records_audit_schema_once():
    connection = FakeConnection()
    applied = apply_postgres_migrations(connection)

    assert applied == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19)
    assert connection.commits == 1
    assert connection.rollbacks == 0
    sql = [statement[0] for statement in connection.db_cursor.statements]
    assert "SELECT pg_advisory_xact_lock(%s)" in sql
    migration_sql = next(statement for statement in sql if "CREATE TABLE audit_events" in statement)
    guard_sql = next(statement for statement in sql if "guard_audit_event_insert" in statement)
    registry_sql = next(statement for statement in sql if "CREATE TABLE sources" in statement)
    ingestion_sql = next(statement for statement in sql if "CREATE TABLE collection_observations" in statement)
    story_sql = next(statement for statement in sql if "CREATE TABLE story_decisions" in statement)
    perspective_sql = next(statement for statement in sql if "CREATE TABLE perspectives" in statement)
    learning_sql = next(statement for statement in sql if "CREATE TABLE learning_objects" in statement)
    benchmark_sql = next(statement for statement in sql if "CREATE TABLE benchmark_artifacts" in statement)
    assignment_sql = next(statement for statement in sql if "CREATE TABLE benchmark_review_assignments" in statement)
    study_sql = next(statement for statement in sql if "CREATE TABLE benchmark_study_protocols" in statement)
    specialized_learning_sql = next(statement for statement in sql if "learning_claim_has_evidence_provenance" in statement)
    pipeline_run_sql = next(statement for statement in sql if "CREATE TABLE pipeline_runs" in statement)
    checkpoint_sql = next(statement for statement in sql if "CREATE TABLE pipeline_stage_checkpoints" in statement)
    lease_sql = next(statement for statement in sql if "CREATE TABLE pipeline_worker_leases" in statement)
    edition_artifact_sql = next(statement for statement in sql if "CREATE TABLE edition_artifacts" in statement)
    edition_decision_sql = next(statement for statement in sql if "CREATE TABLE edition_decisions" in statement)
    edition_snapshot_guard_sql = next(statement for statement in sql if "edition_domain_records_no_published_insert" in statement)
    release_authorization_sql = next(statement for statement in sql if "CREATE TABLE edition_release_authorizations" in statement)
    staging_sql = next(statement for statement in sql if "CREATE TABLE edition_staging_events" in statement)
    assert "CREATE TABLE audit_chain_head" in migration_sql
    assert "audit_events_no_update" in migration_sql
    assert "audit_events_no_delete" in migration_sql
    assert "audit_chain_head_update_guard" in guard_sql
    assert "audit_chain_head_no_delete" in guard_sql
    assert "CREATE TABLE permission_grants" in registry_sql
    assert "CREATE TABLE grant_endpoints" in registry_sql
    assert "CREATE TABLE domain_record_revisions" in registry_sql
    assert "ON DELETE RESTRICT" in registry_sql
    assert "domain_record_revisions_no_update" in registry_sql
    assert "CREATE TABLE ingestion_decisions" in ingestion_sql
    assert "CREATE TABLE ingestion_current" in ingestion_sql
    assert "validate_ingestion_decision" in ingestion_sql
    assert "collection_observations_no_update" in ingestion_sql
    assert "CREATE TABLE story_decision_applications" in story_sql
    assert "CREATE TABLE timeline_events" in story_sql
    assert "validate_timeline_event_insert" in story_sql
    assert "timeline_events_no_update" in story_sql
    assert "CREATE TABLE claim_relations" in perspective_sql
    assert "validate_perspective_insert" in perspective_sql
    assert "validate_claim_relation_insert" in perspective_sql
    assert "perspectives_no_update" in perspective_sql
    assert "CREATE TABLE learning_object_revisions" in learning_sql
    assert "CREATE TABLE glossary_entries" in learning_sql
    assert "record_learning_object_revision" in learning_sql
    assert "record_glossary_entry_revision" in learning_sql
    assert "CREATE TABLE benchmark_reviews" in benchmark_sql
    assert "CREATE TABLE benchmark_review_revisions" in benchmark_sql
    assert "benchmark_adjudications_one_per_artifact" in benchmark_sql
    assert "benchmark_artifacts_no_update" in benchmark_sql
    assert "benchmark_review_assignment_one_active_per_reviewer" in assignment_sql
    assert "benchmark_review_assignments_record_revision" in assignment_sql
    assert "benchmark_review_identity_attestations" in assignment_sql
    assert "benchmark_scores_no_update" in assignment_sql
    assert "benchmark_study_protocols_no_update" in study_sql
    assert "benchmark_study_executions_no_update" in study_sql
    assert "benchmark_acceptance_reviews_no_update" in study_sql
    assert "learning_objects_specialized_evidence_insert" in specialized_learning_sql
    assert "learning_objects_specialized_evidence_update" in specialized_learning_sql
    assert "question answer evidence must cover every answer claim" in specialized_learning_sql
    assert "pipeline_runs_validate_write" in pipeline_run_sql
    assert "pipeline_run_revisions_no_update" in pipeline_run_sql
    assert "pipeline run revision must match current record" in pipeline_run_sql
    assert "pipeline_stage_checkpoint_revision_snapshot" in checkpoint_sql
    assert "pipeline_stage_checkpoint_revisions_no_update" in checkpoint_sql
    assert "provider operation reference is immutable once recorded" in checkpoint_sql
    assert "pipeline_worker_lease_revision_snapshot" in lease_sql
    assert "pipeline_worker_lease_revisions_no_update" in lease_sql
    assert "fencing_token = OLD.fencing_token + 1" in lease_sql
    assert "edition_artifacts_no_update" in edition_artifact_sql
    assert "stored edition artifact columns must match manifest" in edition_artifact_sql
    assert "edition_decisions_no_update" in edition_decision_sql
    assert "active human approver" in edition_decision_sql
    assert "published editions require the controlled release workflow" in edition_snapshot_guard_sql
    assert "edition_release_authorizations_no_update" in release_authorization_sql
    assert "independent active human authorizer" in release_authorization_sql
    assert "edition_staging_events_insert_guard" in staging_sql
    assert "edition_staging_state_update_guard" in staging_sql
    assert "staging event history is append-only" in staging_sql
    assert connection.db_cursor.rows[0][0:2] == (1, "0001_audit_chain.sql")
    assert connection.db_cursor.rows[1][0:2] == (2, "0002_audit_head_guards.sql")
    assert connection.db_cursor.rows[2][0:2] == (3, "0003_source_permission_domain_registry.sql")
    assert connection.db_cursor.rows[3][0:2] == (4, "0004_collection_ingestion.sql")
    assert connection.db_cursor.rows[4][0:2] == (5, "0005_story_timeline.sql")
    assert connection.db_cursor.rows[5][0:2] == (6, "0006_perspectives_claim_relations.sql")
    assert connection.db_cursor.rows[6][0:2] == (7, "0007_learning_glossary_revisions.sql")
    assert connection.db_cursor.rows[7][0:2] == (8, "0008_benchmark_review_core.sql")
    assert connection.db_cursor.rows[8][0:2] == (9, "0009_benchmark_assignments_identity_scores.sql")
    assert connection.db_cursor.rows[9][0:2] == (10, "0010_benchmark_study_evidence.sql")
    assert connection.db_cursor.rows[10][0:2] == (11, "0011_learning_specialized_evidence.sql")
    assert connection.db_cursor.rows[11][0:2] == (12, "0012_pipeline_runs.sql")
    assert connection.db_cursor.rows[12][0:2] == (13, "0013_pipeline_stage_checkpoints.sql")
    assert connection.db_cursor.rows[13][0:2] == (14, "0014_pipeline_worker_leases.sql")
    assert connection.db_cursor.rows[14][0:2] == (15, "0015_edition_artifacts.sql")
    assert connection.db_cursor.rows[15][0:2] == (16, "0016_edition_decisions.sql")
    assert connection.db_cursor.rows[16][0:2] == (17, "0017_edition_snapshot_guards.sql")
    assert connection.db_cursor.rows[17][0:2] == (18, "0018_edition_release_authorizations.sql")
    assert connection.db_cursor.rows[18][0:2] == (19, "0019_edition_staging_state.sql")
    assert all(row[2].startswith("sha256:") for row in connection.db_cursor.rows)

    applied_again = apply_postgres_migrations(connection)
    assert applied_again == ()
    assert connection.commits == 2
    assert len(connection.db_cursor.rows) == 19


def test_postgres_migration_runner_rejects_modified_applied_migration():
    connection = FakeConnection(rows=[
        (1, "0001_audit_chain.sql", "sha256:" + "0" * 64)
    ])
    with pytest.raises(PostgresMigrationError, match="checksum differs"):
        apply_postgres_migrations(connection)
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_postgres_migration_runner_rejects_unknown_future_schema_version():
    connection = FakeConnection(rows=[
        (99, "future.sql", "sha256:" + "a" * 64)
    ])
    with pytest.raises(PostgresMigrationError, match="unsupported"):
        apply_postgres_migrations(connection)
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_postgres_migration_runner_rejects_noncontiguous_migration_ledger(monkeypatch):
    import sngso_engine.postgres_migration_runner as migration_runner

    monkeypatch.setattr(
        migration_runner,
        "_MIGRATIONS",
        (
            (1, "0001_audit_chain.sql"),
            (2, "0002_registry_core.sql"),
            (3, "0003_staging.sql"),
        ),
    )
    connection = FakeConnection(rows=[
        (1, "0001_audit_chain.sql", "sha256:" + "1" * 64),
        (3, "0003_staging.sql", "sha256:" + "3" * 64),
    ])
    with pytest.raises(PostgresMigrationError, match="not a contiguous prefix"):
        migration_runner.apply_postgres_migrations(connection)
    assert connection.rollbacks == 1
    assert connection.commits == 0
