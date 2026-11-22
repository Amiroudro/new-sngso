from __future__ import annotations

import json
import sqlite3

import pytest

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
    assert report["integrity_check"] == ["ok"]
    assert report["foreign_key_violations"] == []
    assert report["healthy"] is True
    assert report["object_counts"]["table"] >= 47
    assert report["object_counts"]["trigger"] >= 131
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
            "INSERT INTO sources(source_id, name, publisher, base_domain, "
            "language, active, created_at) VALUES "
            "('orphan-source', 'Orphan', 'Orphan', 'example.org', 'en', 1, "
            "'2026-01-01T00:00:00Z')"
        )
        registry._connection.execute("PRAGMA foreign_keys = ON")

    report = inspect_sqlite_schema(database)

    assert report["healthy"] is False
    assert report["foreign_key_violations"]
