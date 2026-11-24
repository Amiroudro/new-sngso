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
        "migration_ledger_matches_packaged": True,
        "outcome": "INCOMPLETE",
    }
    inventory["inventory_sha256"] = _fingerprint(inventory)
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
    return manifest, inventory, mapping


def test_reconciliation_planner_validates_complete_structure_but_never_declares_ready():
    manifest, inventory, mapping = _inputs()
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)

    assert result["structure_valid"] is True
    assert result["outcome"] == "INCOMPLETE"
    assert result["source_table_count"] == 1
    assert result["mapped_source_table_count"] == 1
    assert result["issues"] == []
    assert result["remaining_gates"] == [
        "ROW_VALUE_CONVERSION_PROOF",
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
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)
    codes = {issue["code"] for issue in result["issues"]}

    assert result["structure_valid"] is False
    assert "SOURCE_COLUMNS_UNMAPPED" in codes
    assert "SOURCE_TABLE_UNKNOWN" in codes


def test_reconciliation_planner_rejects_conversion_type_and_primary_key_mismatch():
    manifest, inventory, mapping = _inputs()
    mapping["tables"][0]["columns"][0]["conversion"] = "blob_to_bytea"
    mapping["tables"][0]["columns"][0]["target_column"] = "title"
    result = plan_schema_v36_reconciliation(manifest, inventory, mapping)
    codes = {issue["code"] for issue in result["issues"]}

    assert "CONVERSION_TARGET_TYPE_MISMATCH" in codes
    assert "PRIMARY_KEY_MAPPING_MISMATCH" in codes
    assert "TARGET_COLUMN_MAPPED_MORE_THAN_ONCE" in codes


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
