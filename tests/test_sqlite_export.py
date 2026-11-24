from __future__ import annotations

import sqlite3

import pytest

from sngso_engine.registry import SQLiteRegistry
from sngso_engine.sqlite_export import (
    CHECKSUM_FORMAT,
    MANIFEST_FORMAT,
    SQLiteExportError,
    build_sqlite_export_manifest,
    encode_sqlite_value,
    write_manifest_new,
)


def test_sqlite_value_encoder_preserves_storage_classes_and_boundaries():
    values = [
        None, 0, -1, 2**63 - 1, 1.5, -0.0, "", "বাংলা\x00text",
        b"", b"\x00\xff",
    ]
    encoded = [encode_sqlite_value(value) for value in values]

    assert len(set(encoded)) == len(encoded)
    assert encoded[0][:1] == b"N"
    assert encoded[1][:1] == b"I"
    assert encoded[4][:1] == b"R"
    assert encoded[6][:1] == b"T"
    assert encoded[9][:1] == b"B"
    assert len(encode_sqlite_value(1.5)) == 17
    assert encode_sqlite_value(-0.0) != encode_sqlite_value(0.0)


def test_encoder_rejects_non_sqlite_or_non_finite_values():
    with pytest.raises(TypeError, match="bool"):
        encode_sqlite_value(True)
    with pytest.raises(SQLiteExportError, match="non-finite"):
        encode_sqlite_value(float("nan"))
    with pytest.raises(SQLiteExportError, match="non-finite"):
        encode_sqlite_value(float("inf"))


def test_manifest_is_deterministic_across_batch_sizes_and_read_only(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database) as registry:
        with registry._transaction() as connection:
            registry._append_audit(
                connection,
                event_type="fixture.created",
                entity_type="fixture",
                entity_id="one",
                payload={"text": "বাংলা", "number": 7},
            )
            registry._append_audit(
                connection,
                event_type="fixture.created",
                entity_type="fixture",
                entity_id="two",
                payload={"text": "embedded\x00nul", "number": 8},
            )

    before = database.read_bytes()
    one = build_sqlite_export_manifest(database, batch_size=1)
    many = build_sqlite_export_manifest(database, batch_size=127)
    after = database.read_bytes()

    assert before == after
    assert one == many
    assert one["format"] == MANIFEST_FORMAT
    assert one["checksum_format"] == CHECKSUM_FORMAT
    assert one["source"]["schema_version"] == 36
    assert one["source"]["object_counts"] == {
        "table": 47, "index": 25, "trigger": 139, "view": 0
    }
    assert one["source"]["audit_checkpoint"]["sequence"] == 2
    assert len(one["tables"]) == 47
    audit = next(table for table in one["tables"] if table["table"] == "audit_events")
    assert audit["row_count"] == 2
    assert len(audit["row_sha256"]) == 64
    assert len(one["manifest_sha256"]) == 64


def test_manifest_rejects_wrong_schema_version_and_missing_source(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass

    with pytest.raises(SQLiteExportError, match="does not match expected"):
        build_sqlite_export_manifest(database, expected_schema_version=35)
    with pytest.raises(SQLiteExportError, match="does not exist"):
        build_sqlite_export_manifest(tmp_path / "missing.sqlite")


def test_manifest_rejects_invalid_batch_size(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass

    with pytest.raises(SQLiteExportError, match="batch_size"):
        build_sqlite_export_manifest(database, batch_size=0)
    with pytest.raises(SQLiteExportError, match="batch_size"):
        build_sqlite_export_manifest(database, batch_size=True)


def test_manifest_writer_is_atomic_and_refuses_overwrite(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass
    manifest = build_sqlite_export_manifest(database)
    output = write_manifest_new(tmp_path / "manifest.json", manifest)

    assert output.exists()
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_manifest_new(output, manifest)


def test_export_refuses_table_without_declared_primary_key(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteRegistry(database):
        pass
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE unsafe_unkeyed (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SQLiteExportError, match="object inventory mismatch"):
        build_sqlite_export_manifest(database)
