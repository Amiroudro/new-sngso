from __future__ import annotations

import hashlib

import pytest

from sngso_engine.canonical import canonical_json_bytes
from sngso_engine.registry import SQLiteRegistry
from sngso_engine.sqlite_export import build_sqlite_export_manifest
from sngso_engine.sqlite_json_validation import (
    SQLiteJSONValidationError,
    validate_sqlite_jsonb_content,
)


def _pin(value, field):
    value[field] = hashlib.sha256(
        canonical_json_bytes({k: v for k, v in value.items() if k != field})
    ).hexdigest()
    return value


def _fixture(tmp_path, json_value):
    database = tmp_path / "json-source.sqlite"
    with SQLiteRegistry(database) as registry:
        with registry._transaction() as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                """INSERT INTO sources
                   (id, publisher_name, status, created_at, updated_at, payload_json)
                   VALUES ('fixture', 'Fixture', 'active', '2026-11-24', '2026-11-24', ?)""",
                (json_value,),
            )
    manifest = build_sqlite_export_manifest(database)
    mapping = {
        "format": "sngso-sqlite-to-postgres-mapping-v1",
        "source_manifest_sha256": manifest["manifest_sha256"],
        "source_schema_sha256": manifest["source"]["schema_sha256"],
        "target_inventory_sha256": "0" * 64,
        "review": {"status": "approved", "reviewer": "test", "reviewed_at": "2026-11-24", "reference": "fixture"},
        "tables": [{
            "source_table": "sources",
            "target_table": "sources",
            "columns": [
                {"source_column": "id", "target_column": "id", "conversion": "text_identity"},
                {"source_column": "payload_json", "target_column": "payload_json", "conversion": "json_text_to_jsonb"},
            ],
        }],
    }
    _pin(mapping, "mapping_sha256")
    return database, manifest, mapping


def test_jsonb_content_proof_validates_every_non_null_value(tmp_path):
    database, manifest, mapping = _fixture(tmp_path, '{"বাংলা":[1,true,null]}')
    proof = validate_sqlite_jsonb_content(database, manifest, mapping, batch_size=1)
    assert proof["outcome"] == "VALID"
    column = proof["table_proofs"][0]["columns"][0]
    assert column["source_column"] == "payload_json"
    assert column["validated_non_null"] == 1
    assert column["invalid"] == 0
    assert len(proof["proof_sha256"]) == 64


@pytest.mark.parametrize("value", ['{"broken":', "NaN", '"\\ud800"', '"\\u0000"', '[Infinity]', "1e1000000", "1e-20000"])
def test_jsonb_content_proof_fails_closed_on_invalid_json(tmp_path, value):
    database, manifest, mapping = _fixture(tmp_path, value)
    proof = validate_sqlite_jsonb_content(database, manifest, mapping)
    assert proof["outcome"] == "INVALID"
    assert proof["table_proofs"][0]["columns"][0]["invalid"] == 1


def test_jsonb_content_proof_rejects_source_drift_and_unapproved_mapping(tmp_path):
    database, manifest, mapping = _fixture(tmp_path, '{"ok":true}')
    with SQLiteRegistry(database) as registry:
        with registry._transaction() as connection:
            connection.execute(
                "UPDATE sources SET payload_json=? WHERE id='fixture'",
                ('{"changed":true}',),
            )
    with pytest.raises(SQLiteJSONValidationError, match="differs from pinned manifest"):
        validate_sqlite_jsonb_content(database, manifest, mapping)

    database.unlink()
    database, manifest, mapping = _fixture(tmp_path, '{"ok":true}')
    mapping["review"]["status"] = "draft"
    _pin(mapping, "mapping_sha256")
    with pytest.raises(SQLiteJSONValidationError, match="approval"):
        validate_sqlite_jsonb_content(database, manifest, mapping)
