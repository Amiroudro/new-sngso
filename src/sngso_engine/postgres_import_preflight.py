"""Read-only preflight proving an isolated PostgreSQL import target is empty."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .canonical import canonical_json_bytes


class ImportTargetPreflightError(RuntimeError):
    """Raised when target emptiness cannot be established safely."""


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ImportTargetPreflightError("target SQL identifier is invalid")
    return '"' + value.replace('"', '""') + '"'


def inspect_empty_import_target(
    connection: Any,
    target_inventory: dict[str, Any],
) -> dict[str, Any]:
    """Count target rows in one REPEATABLE READ READ ONLY catalog/data snapshot.

    Migration history is allowed only when it exactly matches the inspected
    packaged migration ledger. The singleton audit head must be at sequence 0,
    and every other table in the active schema must contain zero rows.
    """
    if not isinstance(target_inventory, dict):
        raise ImportTargetPreflightError("target inventory is required")
    supplied_inventory_sha = target_inventory.get("inventory_sha256")
    inventory_body = dict(target_inventory)
    for key in ("inventory_sha256", "outcome", "reason_codes"):
        inventory_body.pop(key, None)
    inventory_bytes = json.dumps(
        inventory_body, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    actual_inventory_sha = hashlib.sha256(inventory_bytes).hexdigest()
    if supplied_inventory_sha != actual_inventory_sha:
        raise ImportTargetPreflightError("target inventory fingerprint mismatch")

    info = getattr(connection, "info", None)
    status = getattr(getattr(info, "transaction_status", None), "name", None)
    if status != "IDLE":
        raise ImportTargetPreflightError("target preflight requires an idle connection")
    if not callable(getattr(connection, "transaction", None)) or not callable(
        getattr(connection, "cursor", None)
    ):
        raise TypeError("connection must provide psycopg 3 transaction() and cursor()")

    identity = target_inventory.get("identity", {})
    schema = identity.get("schema_name")
    database = identity.get("database_name")
    if not isinstance(schema, str) or not schema or not isinstance(database, str) or not database:
        raise ImportTargetPreflightError("target inventory lacks database/schema identity")
    quoted_schema = _quote_identifier(schema)
    issues: list[dict[str, Any]] = []
    table_counts: list[dict[str, Any]] = []
    migration_ledger_matches = False
    audit_head_initial = False

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute(
                    "SELECT current_database(), current_schema(), "
                    "current_setting('server_version_num')::integer"
                )
                current_database, current_schema, server_version_num = cursor.fetchone()
                if current_database != database or current_schema != schema:
                    issues.append({"code": "TARGET_IDENTITY_CHANGED"})

                cursor.execute(
                    """SELECT c.relname
                       FROM pg_class c
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
                       ORDER BY c.relname""",
                    (schema,),
                )
                current_tables = {row[0] for row in cursor.fetchall()}
                inspected_tables = {
                    item.get("table_name")
                    for item in target_inventory.get("tables", [])
                    if isinstance(item, dict)
                }
                if current_tables != inspected_tables:
                    issues.append({"code": "TARGET_TABLE_SET_CHANGED"})

                expected_migrations = target_inventory.get("expected_migrations", [])
                if "postgres_schema_migrations" not in current_tables:
                    issues.append({"code": "TARGET_MIGRATION_LEDGER_MISSING"})
                else:
                    cursor.execute(
                        f"SELECT version, name, checksum FROM {quoted_schema}."
                        '"postgres_schema_migrations" ORDER BY version'
                    )
                    actual_ledger = cursor.fetchall()
                    expected_ledger = [
                        (row["version"], row["name"], row["checksum"])
                        for row in expected_migrations
                    ]
                    migration_ledger_matches = (
                        bool(target_inventory.get("migration_ledger_matches_packaged"))
                        and actual_ledger == expected_ledger
                    )
                    if not migration_ledger_matches:
                        issues.append({"code": "TARGET_MIGRATION_LEDGER_DIVERGED"})
                    table_counts.append({
                        "table_name": "postgres_schema_migrations",
                        "row_count": len(actual_ledger),
                        "excluded_as_schema_metadata": True,
                    })

                if "audit_chain_head" not in current_tables:
                    issues.append({"code": "TARGET_AUDIT_HEAD_MISSING"})
                else:
                    cursor.execute(
                        f"SELECT singleton_id, sequence, event_hash FROM {quoted_schema}."
                        '"audit_chain_head" ORDER BY singleton_id'
                    )
                    heads = cursor.fetchall()
                    audit_head_initial = heads == [(1, 0, None)]
                    if not audit_head_initial:
                        issues.append({"code": "TARGET_AUDIT_HEAD_NOT_EMPTY"})
                    table_counts.append({
                        "table_name": "audit_chain_head",
                        "row_count": len(heads),
                        "initial_state": audit_head_initial,
                    })

                for table_name in sorted(current_tables - {
                    "postgres_schema_migrations", "audit_chain_head"
                }):
                    quoted_table = _quote_identifier(table_name)
                    cursor.execute(
                        f"SELECT count(*) FROM {quoted_schema}.{quoted_table}"
                    )
                    count = int(cursor.fetchone()[0])
                    table_counts.append({"table_name": table_name, "row_count": count})
                    if count:
                        issues.append({
                            "code": "TARGET_TABLE_NOT_EMPTY",
                            "table_name": table_name,
                            "row_count": count,
                        })
    except Exception as exc:
        if isinstance(exc, ImportTargetPreflightError):
            raise
        raise ImportTargetPreflightError(
            "read-only import-target preflight failed"
        ) from exc

    report: dict[str, Any] = {
        "format": "sngso-postgres-empty-import-target-v1",
        "outcome": "EMPTY" if not issues else "NOT_EMPTY_OR_UNVERIFIED",
        "read_only": True,
        "database_name": database,
        "schema_name": schema,
        "server_version_num": server_version_num,
        "target_inventory_sha256": target_inventory.get("inventory_sha256"),
        "migration_ledger_matches_packaged": migration_ledger_matches,
        "audit_head_initial": audit_head_initial,
        "target_empty": not issues,
        "table_counts": table_counts,
        "issue_codes": issues,
    }
    report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    return report
