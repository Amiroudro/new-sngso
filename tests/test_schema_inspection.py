from __future__ import annotations

import sqlite3

import pytest

from sngso_engine import inspect_sqlite_schema as exported_inspector
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.schema_inspection import (
    SchemaInspectionError,
    inspect_sqlite_schema,
)


def test_inspection_reports_actual_migrated_schema_and_integrity(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass

    report = inspect_sqlite_schema(database)

    assert report["format"] == "sngso-sqlite-schema-inventory-v1"
    assert report["schema_version"] == 33
    assert report["migration_history_valid"] is True
    assert [row["version"] for row in report["migration_history"]] == list(range(1, 34))
    assert report["integrity_check"] == ["ok"]
    assert report["foreign_key_violations"] == []
    assert report["audit_chain_valid"] is True
    assert report["healthy"] is True
    assert report["object_counts"] == {"table": 47, "index": 25, "trigger": 131, "view": 0}
    assert len(report["schema_sha256"]) == 64
    names = {item["name"] for item in report["objects"]}
    assert "pipeline_worker_leases" in names
    assert "pipeline_stage_checkpoints" in names
    assert "audit_events" in names
    assert "pipeline_worker_lease_revisions_no_delete" in names


def test_inspection_includes_full_sql_for_manual_constraint_review(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass

    report = inspect_sqlite_schema(database)
    by_name = {item["name"]: item for item in report["objects"]}

    assert "CREATE TABLE" in by_name["pipeline_worker_leases"]["sql"].upper()
    assert "CREATE TRIGGER" in by_name[
        "pipeline_worker_lease_revisions_no_delete"
    ]["sql"].upper()


def test_inspection_is_read_only(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass
    before = database.read_bytes()

    inspect_sqlite_schema(database)

    assert database.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            connection.execute("CREATE TABLE forbidden (id INTEGER)")
        finally:
            connection.close()


def test_inspection_rejects_missing_database_without_creating_it(tmp_path):
    database = tmp_path / "missing.sqlite"

    with pytest.raises(SchemaInspectionError, match="does not exist"):
        inspect_sqlite_schema(database)

    assert not database.exists()


def test_inspection_detects_foreign_key_violations(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database) as registry:
        registry._connection.execute("PRAGMA foreign_keys = OFF")
        registry._connection.execute(
            "INSERT INTO grant_endpoints(grant_id, source_id, endpoint_id) "
            "VALUES ('missing-grant', 'missing-source', 'missing-endpoint')"
        )
        registry._connection.execute("PRAGMA foreign_keys = ON")

    report = inspect_sqlite_schema(database)

    assert report["healthy"] is False
    assert report["foreign_key_violations"]


def test_schema_inspector_is_exported_from_package():
    assert exported_inspector is inspect_sqlite_schema


def test_inspection_detects_migration_history_gap(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database) as registry:
        registry._connection.execute(
            "DELETE FROM schema_migrations WHERE version = 17"
        )

    report = inspect_sqlite_schema(database)

    assert report["migration_history_valid"] is False
    assert report["healthy"] is False


def test_external_audit_checkpoint_detects_tail_truncation(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database) as registry:
        with registry._transaction() as connection:
            registry._append_audit(
                connection,
                event_type="test.event",
                entity_type="test",
                entity_id="entity-1",
                payload={"value": 1},
            )
        expected = registry.audit_checkpoint()

    matching = inspect_sqlite_schema(
        database, expected_audit_checkpoint=expected
    )
    assert matching["external_audit_checkpoint_matches"] is True
    assert matching["healthy"] is True

    with SQLiteRegistry(database) as registry:
        registry._connection.execute("DROP TRIGGER audit_events_no_delete")
        registry._connection.execute("DELETE FROM audit_events")

    truncated = inspect_sqlite_schema(
        database, expected_audit_checkpoint=expected
    )
    assert truncated["audit_chain_valid"] is True
    assert truncated["external_audit_checkpoint_matches"] is False
    assert truncated["healthy"] is False


def test_external_audit_checkpoint_rejects_malformed_values(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass

    with pytest.raises(SchemaInspectionError, match="malformed"):
        inspect_sqlite_schema(
            database, expected_audit_checkpoint={"sequence": True, "event_hash": None}
        )

    with pytest.raises(SchemaInspectionError, match="must contain"):
        inspect_sqlite_schema(
            database, expected_audit_checkpoint={"sequence": 0}
        )
