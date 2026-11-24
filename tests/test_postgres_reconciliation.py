from __future__ import annotations

import hashlib

import pytest

from sngso_engine.canonical import canonical_json_bytes
from sngso_engine.postgres_reconciliation import (
    MAPPING_FORMAT,
    ReconciliationPlanError,
    plan_schema_v36_reconciliation,
)


def _fingerprint(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _inputs():
    manifest = {
        "format": "sngso-registry-import-manifest-v1",
        "checksum_format": "sngso-sqlite-row-stream-v1",
        "source": {
            "schema_version": 36,
            "schema_sha256": "a" * 64,
            "object_counts": {"table": 1, "index": 0, "trigger": 0, "view": 0},
        },
        "tables": [{
            "table": "sample",
            "columns": [
                {"name": "id", "declared_type": "INTEGER", "not_null": True,
                 "default": None, "primary_key_ordinal": 1},
                {"name": "title", "declared_type": "TEXT", "not_null": True,
                 "default": None, "primary_key_ordinal": 0},
            ],
            "primary_key": ["id"],
            "row_count": 2,
            "row_sha256": "b" * 64,
            "schema_sha256": "c" * 64,
            "storage_class_counts": {
                "id": {"null": 0, "integer": 2, "real": 0, "text": 0, "blob": 0},
                "title": {"null": 0, "integer": 0, "real": 0, "text": 2, "blob": 0},
            },
        }],
    }
    manifest["manifest_sha256"] = _fingerprint(manifest)
    inventory = {
        "identity": {"schema_name": "public"},
        "tables": [{"table_name": "sample_pg"}],
        "columns": [
            {"table_name": "sample_pg", "column_name": "id", "data_type": "bigint",
             "nullable": False, "default_expression": None, "identity_kind": "",
             "generated_kind": ""},
            {"table_name": "sample_pg", "column_name": "title", "data_type": "text",
             "nullable": False, "default_expression": None, "identity_kind": "",
             "generated_kind": ""},
        ],
        "primary_keys": [{"table_name": "sample_pg", "columns": ["id"]}],
        "foreign_keys": [],
        "migration_ledger_matches_packaged": True,
        "outcome": "INCOMPLETE",
    }
    inventory["inventory_sha256"] = _fingerprint({
        key: value for key, value in inventory.items()
        if key not in {"outcome", "reason_codes"}
    })
    mapping = {
        "format": MAPPING_FORMAT,
        "source_manifest_sha256": manifest["manifest_sha256"],
        "source_schema_sha256": manifest["source"]["schema_sha256"],
        "target_inventory_sha256": inventory["inventory_sha256"],
        "review": {
            "status": "approved",
            "reviewer": "schema-reviewer",
            "reviewed_at": "2026-11-24T00:00:00Z",
            "reference": "architecture-decision-001",
        },
        "tables": [{
            "source_table": "sample",
            "target_table": "sample_pg",
            "columns": [
                {"source_column": "id", "target_column": "id",
                 "conversion": "integer_to_bigint"},
                {"source_column": "title", "target_column": "title",
                 "conversion": "text_identity"},
            ],
        }],
    }
    mapping["mapping_sha256"] = _fingerprint(mapping)
    return manifest, inventory, mapping


def test_reconciliation_planner_validates_complete_structure_but_never_declares_ready():
    manifest, inventory, mapping = _inputs()
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)

    assert result["structure_valid"] is True
    assert result["outcome"] == "INCOMPLETE"
    assert result["source_table_count"] == 1
    assert result["mapped_source_table_count"] == 1
    assert result["issues"] == []
    assert result["target_import_order"] == ["sample_pg"]
    assert result["deferred_foreign_keys"] == []
    assert result["remaining_gates"] == [
        "JSON_CONTENT_AND_VALUE_CONVERSION_PROOF",
        "TARGET_ROW_COUNT_AND_CHECKSUM_RECONCILIATION",
        "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
    ]


def test_reconciliation_planner_reports_unmapped_table_and_column():
    manifest, inventory, mapping = _inputs()
    mapping["tables"][0]["columns"].pop()
    mapping["tables"].append({
        "source_table": "not_a_source_table",
        "target_table": "sample_pg",
        "columns": [],
    })
    mapping["mapping_sha256"] = _fingerprint({
        key: value for key, value in mapping.items() if key != "mapping_sha256"
    })
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)
    codes = {issue["code"] for issue in result["issues"]}

    assert result["structure_valid"] is False
    assert "SOURCE_COLUMNS_UNMAPPED" in codes
    assert "SOURCE_TABLE_UNKNOWN" in codes


def test_reconciliation_planner_rejects_conversion_type_and_primary_key_mismatch():
    manifest, inventory, mapping = _inputs()
    mapping["tables"][0]["columns"][0]["conversion"] = "blob_to_bytea"
    mapping["tables"][0]["columns"][0]["target_column"] = "title"
    mapping["mapping_sha256"] = _fingerprint({
        key: value for key, value in mapping.items() if key != "mapping_sha256"
    })
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)
    codes = {issue["code"] for issue in result["issues"]}

    assert "CONVERSION_TARGET_TYPE_MISMATCH" in codes
    assert "PRIMARY_KEY_MAPPING_MISMATCH" in codes
    assert "TARGET_COLUMN_MAPPED_MORE_THAN_ONCE" in codes
    assert "SOURCE_STORAGE_CLASS_CONVERSION_UNSAFE" in codes


def test_reconciliation_planner_rejects_tampered_source_or_target_fingerprint():
    manifest, inventory, mapping = _inputs()
    manifest["tables"][0]["row_count"] = 99
    with pytest.raises(ReconciliationPlanError, match="manifest fingerprint"):
        plan_schema_v36_reconciliation(manifest, inventory, mapping)

    manifest, inventory, mapping = _inputs()
    inventory["columns"][0]["data_type"] = "text"
    with pytest.raises(ReconciliationPlanError, match="inventory fingerprint"):
        plan_schema_v36_reconciliation(manifest, inventory, mapping)


def test_reconciliation_planner_requires_mapping_and_current_migration_ledger():
    manifest, inventory, _ = _inputs()
    result = plan_schema_v36_reconciliation(manifest, inventory, None)
    assert result["issues"] == [{"code": "MAPPING_SPEC_MISSING"}]

    inventory["migration_ledger_matches_packaged"] = False
    inventory["inventory_sha256"] = _fingerprint({
        key: value for key, value in inventory.items()
        if key not in {"inventory_sha256", "outcome", "reason_codes"}
    })
    with pytest.raises(ReconciliationPlanError, match="migration ledger"):
        plan_schema_v36_reconciliation(manifest, inventory, None)


def test_reconciliation_planner_rejects_mapping_tampering():
    manifest, inventory, mapping = _inputs()
    mapping["review"]["reviewer"] = "different-reviewer"
    with pytest.raises(ReconciliationPlanError, match="mapping fingerprint"):
        plan_schema_v36_reconciliation(manifest, inventory, mapping)


def test_reconciliation_planner_rejects_nulls_for_non_nullable_target_columns():
    manifest, inventory, mapping = _inputs()
    manifest["tables"][0]["storage_class_counts"]["id"]["null"] = 1
    manifest["tables"][0]["storage_class_counts"]["id"]["integer"] = 1
    manifest["manifest_sha256"] = _fingerprint({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    mapping["source_manifest_sha256"] = manifest["manifest_sha256"]
    mapping["mapping_sha256"] = _fingerprint({
        key: value for key, value in mapping.items() if key != "mapping_sha256"
    })
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)

    assert any(
        issue["code"] == "SOURCE_NULLS_TARGET_NOT_NULL"
        and issue["source_column"] == "id"
        for issue in result["issues"]
    )

def _rehash(manifest, inventory, mapping):
    manifest["manifest_sha256"] = _fingerprint({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    inventory["inventory_sha256"] = _fingerprint({
        key: value for key, value in inventory.items()
        if key not in {"inventory_sha256", "outcome", "reason_codes"}
    })
    mapping["source_manifest_sha256"] = manifest["manifest_sha256"]
    mapping["source_schema_sha256"] = manifest["source"]["schema_sha256"]
    mapping["target_inventory_sha256"] = inventory["inventory_sha256"]
    mapping["mapping_sha256"] = _fingerprint({
        key: value for key, value in mapping.items() if key != "mapping_sha256"
    })


def test_reconciliation_planner_orders_parent_tables_before_children():
    manifest, inventory, mapping = _inputs()
    manifest["tables"].append({
        "table": "child",
        "columns": [
            {"name": "id", "declared_type": "INTEGER", "not_null": True,
             "default": None, "primary_key_ordinal": 1},
            {"name": "parent_id", "declared_type": "INTEGER", "not_null": True,
             "default": None, "primary_key_ordinal": 0},
        ],
        "primary_key": ["id"],
        "row_count": 1,
        "row_sha256": "d" * 64,
        "schema_sha256": "e" * 64,
        "storage_class_counts": {
            "id": {"null": 0, "integer": 1, "real": 0, "text": 0, "blob": 0},
            "parent_id": {"null": 0, "integer": 1, "real": 0, "text": 0, "blob": 0},
        },
    })
    inventory["tables"].append({"table_name": "child_pg"})
    inventory["columns"].extend([
        {"table_name": "child_pg", "column_name": "id", "data_type": "bigint",
         "nullable": False, "default_expression": None, "identity_kind": "",
         "generated_kind": ""},
        {"table_name": "child_pg", "column_name": "parent_id", "data_type": "bigint",
         "nullable": False, "default_expression": None, "identity_kind": "",
         "generated_kind": ""},
    ])
    inventory["primary_keys"].append({"table_name": "child_pg", "columns": ["id"]})
    inventory["foreign_keys"].append({
        "table_name": "child_pg",
        "constraint_name": "child_parent_fk",
        "referenced_schema": "public",
        "referenced_table": "sample_pg",
        "columns": ["parent_id"],
        "referenced_columns": ["id"],
        "deferrable": False,
        "initially_deferred": False,
        "validated": True,
    })
    mapping["tables"].insert(0, {
        "source_table": "child",
        "target_table": "child_pg",
        "columns": [
            {"source_column": "id", "target_column": "id",
             "conversion": "integer_to_bigint"},
            {"source_column": "parent_id", "target_column": "parent_id",
             "conversion": "integer_to_bigint"},
        ],
    })
    _rehash(manifest, inventory, mapping)

    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)

    assert result["structure_valid"] is True
    assert result["target_import_order"] == ["sample_pg", "child_pg"]
    assert result["deferred_foreign_keys"] == []


def test_reconciliation_planner_allows_only_deferrable_foreign_key_cycles():
    manifest, inventory, mapping = _inputs()
    inventory["foreign_keys"] = [{
        "table_name": "sample_pg",
        "constraint_name": "sample_self_fk",
        "referenced_schema": "public",
        "referenced_table": "sample_pg",
        "columns": ["id"],
        "referenced_columns": ["id"],
        "deferrable": True,
        "initially_deferred": False,
        "validated": True,
    }]
    _rehash(manifest, inventory, mapping)

    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)

    assert result["structure_valid"] is True
    assert result["target_import_order"] == ["sample_pg"]
    assert result["deferred_foreign_keys"] == [{
        "table_name": "sample_pg",
        "constraint_name": "sample_self_fk",
    }]


def test_reconciliation_planner_rejects_non_deferrable_foreign_key_cycles():
    manifest, inventory, mapping = _inputs()
    inventory["foreign_keys"] = [{
        "table_name": "sample_pg",
        "constraint_name": "sample_self_fk",
        "referenced_schema": "public",
        "referenced_table": "sample_pg",
        "columns": ["id"],
        "referenced_columns": ["id"],
        "deferrable": False,
        "initially_deferred": False,
        "validated": True,
    }]
    _rehash(manifest, inventory, mapping)

    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)

    assert result["structure_valid"] is False
    assert result["target_import_order"] == []
    assert {
        "code": "NON_DEFERRABLE_FOREIGN_KEY_CYCLE",
        "constraints": [("sample_pg", "sample_self_fk")],
    } in result["issues"]

