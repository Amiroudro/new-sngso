"""Read-only PostgreSQL target inventory for the schema-v36 import boundary.

This module deliberately inventories rather than mutates. It does not infer
SQLite-to-PostgreSQL table mappings and cannot declare an import READY.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .postgres_migration_runner import _MIGRATIONS, _migration_bytes


class PostgresInspectionError(RuntimeError):
    """Raised when a PostgreSQL target cannot be safely inventoried."""


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [item.name for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def inspect_postgres_import_target(connection: Any) -> dict[str, Any]:
    """Capture a deterministic, read-only inventory of the active target schema.

    The supplied psycopg 3 connection must be idle. All queries execute in one
    READ ONLY, REPEATABLE READ transaction so the report describes one catalog
    snapshot. Missing or divergent migration metadata is reported, not repaired.
    """
    info = getattr(connection, "info", None)
    status = getattr(getattr(info, "transaction_status", None), "name", None)
    if status != "IDLE":
        raise PostgresInspectionError(
            "PostgreSQL target inspection requires an idle connection"
        )
    transaction = getattr(connection, "transaction", None)
    cursor_factory = getattr(connection, "cursor", None)
    if not callable(transaction) or not callable(cursor_factory):
        raise TypeError("connection must provide psycopg transaction() and cursor()")

    try:
        with transaction():
            with cursor_factory() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                cursor.execute(
                    """SELECT current_database() AS database_name,
                              current_user AS database_user,
                              current_schema() AS schema_name,
                              current_setting('server_version') AS server_version,
                              current_setting('server_version_num')::integer AS server_version_num,
                              to_regclass('postgres_schema_migrations') IS NOT NULL AS has_migration_ledger,
                              to_regclass('audit_chain_head') IS NOT NULL AS has_audit_head"""
                )
                identity = _rows(cursor)[0]
                schema_name = identity["schema_name"]
                if schema_name is None:
                    raise PostgresInspectionError(
                        "PostgreSQL search_path has no writable current schema"
                    )

                cursor.execute(
                    """SELECT c.relname AS table_name, c.relkind AS relation_kind,
                              obj_description(c.oid, 'pg_class') AS comment
                       FROM pg_class c
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
                       ORDER BY c.relname""",
                    (schema_name,),
                )
                tables = _rows(cursor)

                cursor.execute(
                    """SELECT c.relname AS table_name, a.attnum AS ordinal,
                              a.attname AS column_name,
                              format_type(a.atttypid, a.atttypmod) AS data_type,
                              NOT a.attnotnull AS nullable,
                              pg_get_expr(d.adbin, d.adrelid) AS default_expression,
                              a.attidentity AS identity_kind,
                              a.attgenerated AS generated_kind
                       FROM pg_attribute a
                       JOIN pg_class c ON c.oid = a.attrelid
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       LEFT JOIN pg_attrdef d
                         ON d.adrelid = c.oid AND d.adnum = a.attnum
                       WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
                         AND a.attnum > 0 AND NOT a.attisdropped
                       ORDER BY c.relname, a.attnum""",
                    (schema_name,),
                )
                columns = _rows(cursor)

                cursor.execute(
                    """SELECT c.relname AS table_name, con.conname AS constraint_name,
                              con.contype AS constraint_type,
                              pg_get_constraintdef(con.oid, true) AS definition,
                              con.condeferrable AS deferrable,
                              con.condeferred AS initially_deferred,
                              con.convalidated AS validated
                       FROM pg_constraint con
                       JOIN pg_class c ON c.oid = con.conrelid
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = %s
                       ORDER BY c.relname, con.conname""",
                    (schema_name,),
                )
                constraints = _rows(cursor)

                cursor.execute(
                    """SELECT t.relname AS table_name, i.relname AS index_name,
                              pg_get_indexdef(i.oid) AS definition
                       FROM pg_index x
                       JOIN pg_class t ON t.oid = x.indrelid
                       JOIN pg_class i ON i.oid = x.indexrelid
                       JOIN pg_namespace n ON n.oid = t.relnamespace
                       WHERE n.nspname = %s
                       ORDER BY t.relname, i.relname""",
                    (schema_name,),
                )
                indexes = _rows(cursor)

                cursor.execute(
                    """SELECT c.relname AS table_name, t.tgname AS trigger_name,
                              t.tgenabled AS enabled_state,
                              pg_get_triggerdef(t.oid, true) AS definition,
                              p.proname AS function_name,
                              pg_get_functiondef(p.oid) AS function_definition
                       FROM pg_trigger t
                       JOIN pg_class c ON c.oid = t.tgrelid
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       JOIN pg_proc p ON p.oid = t.tgfoid
                       WHERE n.nspname = %s AND NOT t.tgisinternal
                       ORDER BY c.relname, t.tgname""",
                    (schema_name,),
                )
                triggers = _rows(cursor)

                ledger = []
                ledger_present = bool(identity["has_migration_ledger"])
                if ledger_present:
                    cursor.execute(
                        """SELECT version, name, checksum, applied_at::text AS applied_at
                           FROM postgres_schema_migrations ORDER BY version"""
                    )
                    ledger = _rows(cursor)

                expected = []
                for version, name in _MIGRATIONS:
                    digest = hashlib.sha256(_migration_bytes(name)).hexdigest()
                    expected.append({
                        "version": version,
                        "name": name,
                        "checksum": "sha256:" + digest,
                    })
                ledger_matches = ledger_present and [
                    (r["version"], r["name"], r["checksum"]) for r in ledger
                ] == [
                    (r["version"], r["name"], r["checksum"]) for r in expected
                ]

                audit_head = None
                if identity["has_audit_head"]:
                    cursor.execute(
                        "SELECT singleton_id, sequence, event_hash "
                        "FROM audit_chain_head ORDER BY singleton_id"
                    )
                    audit_head = [
                        {"singleton_id": row[0], "sequence": row[1], "event_hash": row[2]}
                        for row in cursor.fetchall()
                    ]

                inventory = {
                    "identity": identity,
                    "tables": tables,
                    "columns": columns,
                    "constraints": constraints,
                    "indexes": indexes,
                    "triggers": triggers,
                    "migration_ledger": ledger,
                    "expected_migrations": expected,
                    "migration_ledger_matches_packaged": ledger_matches,
                    "audit_chain_head": audit_head,
                }
                canonical = json.dumps(
                    inventory, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8")
                inventory["inventory_sha256"] = hashlib.sha256(canonical).hexdigest()

                # No reviewed schema-v36 source/target mapping exists yet.
                # Consequently this inventory can never assert import readiness.
                inventory["outcome"] = (
                    "INCOMPLETE" if not ledger_matches or not identity["has_audit_head"]
                    else "INCOMPLETE"
                )
                inventory["reason_codes"] = [
                    "SOURCE_TARGET_MAPPING_NOT_REVIEWED",
                ]
                if not ledger_present:
                    inventory["reason_codes"].append("POSTGRES_MIGRATION_LEDGER_MISSING")
                elif not ledger_matches:
                    inventory["reason_codes"].append("POSTGRES_MIGRATION_LEDGER_DIVERGED")
                if not identity["has_audit_head"]:
                    inventory["reason_codes"].append("POSTGRES_AUDIT_HEAD_MISSING")
                if any(not item["validated"] for item in constraints):
                    inventory["reason_codes"].append("UNVALIDATED_POSTGRES_CONSTRAINTS")
                return inventory
    except PostgresInspectionError:
        raise
    except Exception as exc:
        raise PostgresInspectionError(
            "PostgreSQL target inspection failed; no target writes were attempted"
        ) from exc
