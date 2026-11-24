"""Unapproved exact-name suggestions for reviewer-owned schema-v36 mappings."""
from __future__ import annotations

import hashlib
from typing import Any

from .canonical import canonical_json_bytes
from .postgres_reconciliation import (
    CONVERSION_TARGET_TYPES,
    MAPPING_FORMAT,
    ReconciliationPlanError,
    _digest,
    _target_inventory_digest,
)
from .sqlite_export import EXPECTED_SCHEMA_VERSION, MANIFEST_FORMAT


def _candidate_conversion(declared_type: Any, target_type: Any) -> str | None:
    if not isinstance(declared_type, str) or not isinstance(target_type, str):
        return None
    source = declared_type.strip().upper()
    target = target_type.strip().lower()
    if target in CONVERSION_TARGET_TYPES["text_identity"] and any(
        marker in source for marker in ("TEXT", "CHAR", "CLOB")
    ):
        return "text_identity"
    if target in CONVERSION_TARGET_TYPES["integer_to_bigint"] and "INT" in source:
        return "integer_to_bigint"
    if target in CONVERSION_TARGET_TYPES["real_to_double_precision"] and any(
        marker in source for marker in ("REAL", "FLOA", "DOUB")
    ):
        return "real_to_double_precision"
    if target in CONVERSION_TARGET_TYPES["blob_to_bytea"] and "BLOB" in source:
        return "blob_to_bytea"
    if target in CONVERSION_TARGET_TYPES["json_text_to_jsonb"] and any(
        marker in source for marker in ("TEXT", "CHAR", "CLOB")
    ):
        return "json_text_to_jsonb"
    return None


def build_schema_v36_mapping_skeleton(
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
) -> dict[str, Any]:
    """Suggest exact-name table/column mappings without approving any conversion.

    Suggestions are bound to the supplied source and target fingerprints.
    Only identical relation and column names with a known declared-type mapping
    are suggested. The output is marked DRAFT and the normal planner rejects it
    until a human reviewer replaces the review fields and resolves all gaps.
    """
    if (
        not isinstance(source_manifest, dict)
        or source_manifest.get("format") != MANIFEST_FORMAT
        or source_manifest.get("source", {}).get("schema_version") != EXPECTED_SCHEMA_VERSION
        or source_manifest.get("manifest_sha256") != _digest(source_manifest, "manifest_sha256")
    ):
        raise ReconciliationPlanError("verified schema-v36 source manifest is required")
    if (
        not isinstance(target_inventory, dict)
        or target_inventory.get("inventory_sha256") != _target_inventory_digest(target_inventory)
        or not isinstance(target_inventory.get("tables"), list)
        or not isinstance(target_inventory.get("columns"), list)
    ):
        raise ReconciliationPlanError("fingerprinted PostgreSQL target inventory is required")

    target_names = {
        item.get("table_name")
        for item in target_inventory["tables"]
        if isinstance(item, dict) and isinstance(item.get("table_name"), str)
    }
    target_columns: dict[str, dict[str, dict[str, Any]]] = {}
    for item in target_inventory["columns"]:
        if isinstance(item, dict):
            table_name = item.get("table_name")
            column_name = item.get("column_name")
            if isinstance(table_name, str) and isinstance(column_name, str):
                target_columns.setdefault(table_name, {})[column_name] = item

    table_suggestions: list[dict[str, Any]] = []
    unmatched_tables: list[str] = []
    unresolved_columns: list[dict[str, str]] = []
    for source_table in source_manifest.get("tables", []):
        if not isinstance(source_table, dict) or not isinstance(source_table.get("table"), str):
            raise ReconciliationPlanError("source manifest table inventory is malformed")
        source_name = source_table["table"]
        if source_name not in target_names:
            unmatched_tables.append(source_name)
            continue
        columns: list[dict[str, str]] = []
        by_name = target_columns.get(source_name, {})
        source_columns = source_table.get("columns", [])
        if not isinstance(source_columns, list):
            raise ReconciliationPlanError("source manifest column inventory is malformed")
        for source_column in source_columns:
            if not isinstance(source_column, dict) or not isinstance(source_column.get("name"), str):
                raise ReconciliationPlanError("source manifest column entry is malformed")
            column_name = source_column["name"]
            target_column = by_name.get(column_name)
            conversion = (
                _candidate_conversion(
                    source_column.get("declared_type"),
                    target_column.get("data_type"),
                )
                if target_column is not None else None
            )
            if conversion is None:
                unresolved_columns.append({
                    "source_table": source_name,
                    "source_column": column_name,
                })
                continue
            columns.append({
                "source_column": column_name,
                "target_column": column_name,
                "conversion": conversion,
            })
        table_suggestions.append({
            "source_table": source_name,
            "target_table": source_name,
            "columns": columns,
        })

    skeleton: dict[str, Any] = {
        "format": MAPPING_FORMAT,
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "source_schema_sha256": source_manifest["source"]["schema_sha256"],
        "target_inventory_sha256": target_inventory["inventory_sha256"],
        "target_identity": {
            "database_name": target_inventory.get("identity", {}).get("database_name"),
            "schema_name": target_inventory.get("identity", {}).get("schema_name"),
        },
        "review": {
            "status": "draft",
            "reviewer": "",
            "reviewed_at": "",
            "reference": "",
        },
        "tables": table_suggestions,
        "draft_gaps": {
            "unmatched_source_tables": sorted(unmatched_tables),
            "unresolved_exact_name_columns": sorted(
                unresolved_columns,
                key=lambda item: (item["source_table"], item["source_column"]),
            ),
        },
        "draft_notice": (
            "Unreviewed exact-name suggestions only. Confirm table/column semantics, "
            "storage classes, JSON handling, keys, constraints, trigger parity, and "
            "data conversion before setting review.status to approved."
        ),
    }
    skeleton["mapping_sha256"] = hashlib.sha256(
        canonical_json_bytes(skeleton)
    ).hexdigest()
    return skeleton
