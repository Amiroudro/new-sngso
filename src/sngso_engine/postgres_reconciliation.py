"""Fail-closed structural planner for schema-v36 SQLite to PostgreSQL reconciliation.

The planner validates an explicitly reviewed mapping. It never reads row data,
opens a connection, executes SQL, or mutates either database.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .canonical import canonical_json_bytes
from .sqlite_export import MANIFEST_FORMAT, EXPECTED_SCHEMA_VERSION


MAPPING_FORMAT = "sngso-sqlite-to-postgres-mapping-v1"
CONVERSION_TARGET_TYPES = {
    "text_identity": {"text", "character varying", "character"},
    "integer_to_bigint": {"bigint"},
    "real_to_double_precision": {"double precision"},
    "blob_to_bytea": {"bytea"},
    "json_text_to_jsonb": {"jsonb"},
}
CONVERSION_SOURCE_CLASSES = {
    "text_identity": {"text", "null"},
    "integer_to_bigint": {"integer", "null"},
    "real_to_double_precision": {"real", "null"},
    "blob_to_bytea": {"blob", "null"},
    "json_text_to_jsonb": {"text", "null"},
}


class ReconciliationPlanError(ValueError):
    """Raised for malformed or internally inconsistent reconciliation inputs."""


def _digest(value: dict[str, Any], field: str) -> str:
    copy_value = dict(value)
    copy_value.pop(field, None)
    return hashlib.sha256(canonical_json_bytes(copy_value)).hexdigest()


def _target_inventory_digest(inventory: dict[str, Any]) -> str:
    base = dict(inventory)
    for key in ("inventory_sha256", "outcome", "reason_codes"):
        base.pop(key, None)
    return hashlib.sha256(canonical_json_bytes(base)).hexdigest()


def _issue(code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, **details}


def _foreign_key_import_order(
    target_inventory: dict[str, Any],
    mapped_target_tables: set[str],
) -> tuple[list[str], list[dict[str, str]], list[dict[str, Any]]]:
    """Derive a deterministic parent-first load order from inspected target FKs.

    Cyclic edges are removed only when each constraint participating in that
    cycle is explicitly deferrable. The planner never changes constraints.
    """
    foreign_keys = target_inventory.get("foreign_keys")
    if not isinstance(foreign_keys, list):
        return [], [], [_issue("TARGET_FOREIGN_KEY_INVENTORY_MISSING")]

    schema_name = target_inventory.get("identity", {}).get("schema_name")
    if not isinstance(schema_name, str) or not schema_name:
        return [], [], [_issue("TARGET_SCHEMA_IDENTITY_MISSING")]

    issues: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_constraints: set[tuple[str, str]] = set()
    for foreign_key in foreign_keys:
        if not isinstance(foreign_key, dict):
            issues.append(_issue("FOREIGN_KEY_INVENTORY_ENTRY_INVALID"))
            continue
        child = foreign_key.get("table_name")
        if child not in mapped_target_tables:
            continue
        constraint = foreign_key.get("constraint_name")
        parent = foreign_key.get("referenced_table")
        parent_schema = foreign_key.get("referenced_schema")
        deferrable = foreign_key.get("deferrable")
        key = (child, constraint)
        if (
            not isinstance(child, str) or not isinstance(constraint, str) or not constraint
            or not isinstance(parent, str) or not parent
            or not isinstance(parent_schema, str) or not parent_schema
            or not isinstance(deferrable, bool)
        ):
            issues.append(_issue("FOREIGN_KEY_INVENTORY_ENTRY_INVALID", table_name=child))
            continue
        if key in seen_constraints:
            issues.append(_issue(
                "FOREIGN_KEY_CONSTRAINT_DUPLICATED",
                table_name=child,
                constraint_name=constraint,
            ))
            continue
        seen_constraints.add(key)
        if parent_schema != schema_name:
            issues.append(_issue(
                "FOREIGN_KEY_REFERENCES_EXTERNAL_SCHEMA",
                table_name=child,
                constraint_name=constraint,
                referenced_schema=parent_schema,
                expected_schema=schema_name,
            ))
            continue
        if parent not in mapped_target_tables:
            issues.append(_issue(
                "FOREIGN_KEY_PARENT_NOT_MAPPED",
                table_name=child,
                constraint_name=constraint,
                referenced_table=parent,
            ))
            continue
        edges.append({
            "parent": parent,
            "child": child,
            "constraint_name": constraint,
            "deferrable": deferrable,
        })

    if issues:
        return [], [], issues

    deferred: set[tuple[str, str]] = set()
    ordered: list[str] = []
    while True:
        adjacency = {table: set() for table in mapped_target_tables}
        indegree = {table: 0 for table in mapped_target_tables}
        for edge in edges:
            key = (edge["child"], edge["constraint_name"])
            if key in deferred or edge["child"] in adjacency[edge["parent"]]:
                continue
            adjacency[edge["parent"]].add(edge["child"])
            indegree[edge["child"]] += 1

        ready = sorted(table for table, degree in indegree.items() if degree == 0)
        ordered = []
        while ready:
            parent = ready.pop(0)
            ordered.append(parent)
            for child in sorted(adjacency[parent]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()

        if len(ordered) == len(mapped_target_tables):
            break

        residual = {table for table, degree in indegree.items() if degree > 0}

        def reaches(start: str, target: str) -> bool:
            pending = [start]
            visited: set[str] = set()
            while pending:
                current = pending.pop()
                if current == target:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(adjacency[current] - visited)
            return False

        cycle_edges = [
            edge for edge in edges
            if (edge["child"], edge["constraint_name"]) not in deferred
            and edge["parent"] in residual and edge["child"] in residual
            and reaches(edge["child"], edge["parent"])
        ]
        if not cycle_edges:
            return [], [], [_issue("FOREIGN_KEY_IMPORT_ORDER_UNRESOLVED")]

        blocking = [edge for edge in cycle_edges if not edge["deferrable"]]
        if blocking:
            issues.append(_issue(
                "NON_DEFERRABLE_FOREIGN_KEY_CYCLE",
                constraints=sorted(
                    (edge["child"], edge["constraint_name"]) for edge in blocking
                ),
            ))
            return [], [], issues

        deferred.update((edge["child"], edge["constraint_name"]) for edge in cycle_edges)

    deferred_constraints = [
        {"table_name": table, "constraint_name": constraint}
        for table, constraint in sorted(deferred)
    ]
    return ordered, deferred_constraints, issues


def plan_schema_v36_reconciliation(
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
    mapping_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate complete table/column/PK mapping and report remaining gates.

    A structurally valid mapping is not an import authorization. Row-level
    conversion proofs, target row checksums, and imported audit-chain checks
    remain mandatory before a separate readiness decision can be made.
    """
    if not isinstance(source_manifest, dict) or source_manifest.get("format") != MANIFEST_FORMAT:
        raise ReconciliationPlanError("unsupported or missing SQLite source manifest")
    if source_manifest.get("source", {}).get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise ReconciliationPlanError("source manifest is not schema version 36")
    supplied_manifest_digest = source_manifest.get("manifest_sha256")
    if supplied_manifest_digest != _digest(source_manifest, "manifest_sha256"):
        raise ReconciliationPlanError("SQLite source manifest fingerprint mismatch")

    if not isinstance(target_inventory, dict):
        raise ReconciliationPlanError("PostgreSQL target inventory is required")
    if not isinstance(target_inventory.get("foreign_keys"), list):
        raise ReconciliationPlanError("PostgreSQL foreign-key inventory is required")
    if target_inventory.get("inventory_sha256") != _target_inventory_digest(target_inventory):
        raise ReconciliationPlanError("PostgreSQL target inventory fingerprint mismatch")
    if target_inventory.get("outcome") != "INCOMPLETE":
        raise ReconciliationPlanError("target inventory outcome is not fail-closed INCOMPLETE")
    if not target_inventory.get("migration_ledger_matches_packaged"):
        raise ReconciliationPlanError("PostgreSQL migration ledger does not match packaged migrations")

    issues: list[dict[str, Any]] = []
    if mapping_spec is None:
        return {
            "format": "sngso-schema-v36-reconciliation-plan-v1",
            "outcome": "INCOMPLETE",
            "structure_valid": False,
            "issues": [_issue("MAPPING_SPEC_MISSING")],
            "remaining_gates": [
                "REVIEWED_TABLE_COLUMN_MAPPING",
                "JSON_CONTENT_AND_VALUE_CONVERSION_PROOF",
                "TARGET_ROW_COUNT_AND_CHECKSUM_RECONCILIATION",
                "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
            ],
        }
    if not isinstance(mapping_spec, dict) or mapping_spec.get("format") != MAPPING_FORMAT:
        raise ReconciliationPlanError("unsupported reconciliation mapping format")
    supplied_mapping_digest = mapping_spec.get("mapping_sha256")
    if supplied_mapping_digest != _digest(mapping_spec, "mapping_sha256"):
        raise ReconciliationPlanError("reconciliation mapping fingerprint mismatch")

    if mapping_spec.get("source_manifest_sha256") != supplied_manifest_digest:
        issues.append(_issue("MAPPING_SOURCE_MANIFEST_MISMATCH"))
    if mapping_spec.get("source_schema_sha256") != source_manifest["source"].get("schema_sha256"):
        issues.append(_issue("MAPPING_SOURCE_SCHEMA_MISMATCH"))
    if mapping_spec.get("target_inventory_sha256") != target_inventory["inventory_sha256"]:
        issues.append(_issue("MAPPING_TARGET_INVENTORY_MISMATCH"))

    review = mapping_spec.get("review")
    if not isinstance(review, dict) or review.get("status") != "approved" or any(
        not isinstance(review.get(key), str) or not review[key].strip()
        for key in ("reviewer", "reviewed_at", "reference")
    ):
        issues.append(_issue("MAPPING_REVIEW_NOT_APPROVED"))

    source_tables = {item["table"]: item for item in source_manifest.get("tables", [])}
    target_tables = {item["table_name"] for item in target_inventory.get("tables", [])}
    target_columns: dict[str, dict[str, dict[str, Any]]] = {}
    for column in target_inventory.get("columns", []):
        target_columns.setdefault(column["table_name"], {})[column["column_name"]] = column
    target_primary_keys = {
        item["table_name"]: list(item["columns"])
        for item in target_inventory.get("primary_keys", [])
    }

    table_specs = mapping_spec.get("tables")
    if not isinstance(table_specs, list):
        issues.append(_issue("MAPPING_TABLE_LIST_INVALID"))
        table_specs = []

    mapped_sources: set[str] = set()
    mapped_targets: set[str] = set()
    table_results: list[dict[str, Any]] = []
    for table_spec in table_specs:
        if not isinstance(table_spec, dict):
            issues.append(_issue("TABLE_MAPPING_INVALID"))
            continue
        source_name = table_spec.get("source_table")
        target_name = table_spec.get("target_table")
        if not isinstance(source_name, str) or source_name not in source_tables:
            issues.append(_issue("SOURCE_TABLE_UNKNOWN", source_table=source_name))
            continue
        if source_name in mapped_sources:
            issues.append(_issue("SOURCE_TABLE_MAPPED_MORE_THAN_ONCE", source_table=source_name))
            continue
        mapped_sources.add(source_name)
        if not isinstance(target_name, str) or target_name not in target_tables:
            issues.append(_issue("TARGET_TABLE_MISSING", source_table=source_name, target_table=target_name))
            continue
        if target_name in mapped_targets:
            issues.append(_issue("TARGET_TABLE_MAPPED_MORE_THAN_ONCE", target_table=target_name))
            continue
        mapped_targets.add(target_name)

        source_columns = {item["name"] for item in source_tables[source_name].get("columns", [])}
        column_specs = table_spec.get("columns")
        if not isinstance(column_specs, list):
            issues.append(_issue("COLUMN_MAPPING_LIST_INVALID", source_table=source_name))
            column_specs = []
        mapped_source_columns: set[str] = set()
        mapped_target_columns: set[str] = set()
        column_results = []
        for column_spec in column_specs:
            if not isinstance(column_spec, dict):
                issues.append(_issue("COLUMN_MAPPING_INVALID", source_table=source_name))
                continue
            source_column = column_spec.get("source_column")
            target_column = column_spec.get("target_column")
            conversion = column_spec.get("conversion")
            if source_column not in source_columns:
                issues.append(_issue("SOURCE_COLUMN_UNKNOWN", source_table=source_name, source_column=source_column))
                continue
            if source_column in mapped_source_columns:
                issues.append(_issue("SOURCE_COLUMN_MAPPED_MORE_THAN_ONCE", source_table=source_name, source_column=source_column))
                continue
            mapped_source_columns.add(source_column)
            target_column_meta = target_columns.get(target_name, {}).get(target_column)
            if target_column_meta is None:
                issues.append(_issue("TARGET_COLUMN_MISSING", target_table=target_name, target_column=target_column))
                continue
            if target_column in mapped_target_columns:
                issues.append(_issue("TARGET_COLUMN_MAPPED_MORE_THAN_ONCE", target_table=target_name, target_column=target_column))
                continue
            mapped_target_columns.add(target_column)
            accepted_types = CONVERSION_TARGET_TYPES.get(conversion)
            actual_type = target_column_meta["data_type"].lower()
            if accepted_types is None or actual_type not in accepted_types:
                issues.append(_issue(
                    "CONVERSION_TARGET_TYPE_MISMATCH",
                    source_table=source_name,
                    source_column=source_column,
                    target_table=target_name,
                    target_column=target_column,
                    conversion=conversion,
                    actual_type=actual_type,
                ))
            storage_counts = source_tables[source_name].get(
                "storage_class_counts", {}
            ).get(source_column)
            if not isinstance(storage_counts, dict):
                issues.append(_issue(
                    "SOURCE_STORAGE_CLASS_INVENTORY_MISSING",
                    source_table=source_name,
                    source_column=source_column,
                ))
                storage_counts = {}
            else:
                expected_storage_classes = {"null", "integer", "real", "text", "blob"}
                valid_counts = (
                    set(storage_counts) == expected_storage_classes
                    and all(
                        isinstance(count, int) and not isinstance(count, bool) and count >= 0
                        for count in storage_counts.values()
                    )
                    and sum(storage_counts.values()) == source_tables[source_name].get("row_count")
                )
                if not valid_counts:
                    issues.append(_issue(
                        "SOURCE_STORAGE_CLASS_COUNTS_INCOMPLETE",
                        source_table=source_name,
                        source_column=source_column,
                    ))
                allowed_classes = CONVERSION_SOURCE_CLASSES.get(conversion)
                observed_classes = sorted(
                    key for key, count in storage_counts.items()
                    if count and key not in (allowed_classes or set())
                )
                if allowed_classes is None or observed_classes:
                    issues.append(_issue(
                        "SOURCE_STORAGE_CLASS_CONVERSION_UNSAFE",
                        source_table=source_name,
                        source_column=source_column,
                        conversion=conversion,
                        observed_incompatible_classes=observed_classes,
                        storage_class_counts=storage_counts,
                    ))
            if storage_counts.get("null", 0) and not target_column_meta.get("nullable", True):
                issues.append(_issue(
                    "SOURCE_NULLS_TARGET_NOT_NULL",
                    source_table=source_name,
                    source_column=source_column,
                    target_table=target_name,
                    target_column=target_column,
                    null_count=storage_counts["null"],
                ))
            column_results.append({
                "source_column": source_column,
                "target_column": target_column,
                "conversion": conversion,
                "target_type": actual_type,
                "source_storage_class_counts": storage_counts,
            })

        missing_source_columns = sorted(source_columns - mapped_source_columns)
        if missing_source_columns:
            issues.append(_issue(
                "SOURCE_COLUMNS_UNMAPPED",
                source_table=source_name,
                columns=missing_source_columns,
            ))

        required_target_columns = sorted(
            name for name, meta in target_columns.get(target_name, {}).items()
            if name not in mapped_target_columns
            and not meta.get("nullable", True)
            and meta.get("default_expression") is None
            and not meta.get("identity_kind")
            and not meta.get("generated_kind")
        )
        if required_target_columns:
            issues.append(_issue(
                "TARGET_REQUIRED_COLUMNS_UNMAPPED",
                target_table=target_name,
                columns=required_target_columns,
            ))

        source_pk = source_tables[source_name].get("primary_key", [])
        target_pk = target_primary_keys.get(target_name, [])
        mapped_pk = []
        for source_pk_column in source_pk:
            match = next(
                (item["target_column"] for item in column_results
                 if item["source_column"] == source_pk_column),
                None,
            )
            mapped_pk.append(match)
        if mapped_pk != target_pk:
            issues.append(_issue(
                "PRIMARY_KEY_MAPPING_MISMATCH",
                source_table=source_name,
                target_table=target_name,
                mapped_source_primary_key=mapped_pk,
                target_primary_key=target_pk,
            ))

        table_results.append({
            "source_table": source_name,
            "target_table": target_name,
            "source_column_count": len(source_columns),
            "mapped_source_column_count": len(mapped_source_columns),
            "target_primary_key": target_pk,
            "columns": column_results,
        })

    unmapped_tables = sorted(set(source_tables) - mapped_sources)
    if unmapped_tables:
        issues.append(_issue("SOURCE_TABLES_UNMAPPED", tables=unmapped_tables))

    target_import_order, deferred_foreign_keys, foreign_key_issues = _foreign_key_import_order(
        target_inventory,
        mapped_targets,
    )
    issues.extend(foreign_key_issues)
    structure_valid = not issues
    if not structure_valid:
        target_import_order = []
        deferred_foreign_keys = []
    return {
        "format": "sngso-schema-v36-reconciliation-plan-v1",
        "outcome": "INCOMPLETE",
        "structure_valid": structure_valid,
        "source_manifest_sha256": supplied_manifest_digest,
        "target_inventory_sha256": target_inventory["inventory_sha256"],
        "mapping_sha256": supplied_mapping_digest,
        "source_table_count": len(source_tables),
        "mapped_source_table_count": len(mapped_sources),
        "unmapped_source_tables": unmapped_tables,
        "target_import_order": target_import_order,
        "deferred_foreign_keys": deferred_foreign_keys,
        "table_mappings": table_results,
        "issues": issues,
        "remaining_gates": [
            "JSON_CONTENT_AND_VALUE_CONVERSION_PROOF",
            "TARGET_ROW_COUNT_AND_CHECKSUM_RECONCILIATION",
            "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
        ] if structure_valid else [
            "RESOLVE_STRUCTURAL_MAPPING_ISSUES",
            "JSON_CONTENT_AND_VALUE_CONVERSION_PROOF",
            "TARGET_ROW_COUNT_AND_CHECKSUM_RECONCILIATION",
            "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
        ],
    }
