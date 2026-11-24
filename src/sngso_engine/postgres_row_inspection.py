"""Read-only, snapshot-consistent PostgreSQL mapped-row fingerprinting.

This is a target-side evidence collector, not an importer. It hashes only
explicitly mapped columns, in mapped primary-key order, and never returns row
values. Its digest is deliberately target-specific: it is not claimed to equal
the SQLite source digest until a source-side conversion proof is implemented.
"""
from __future__ import annotations

import hashlib
import math
import re
import struct
from typing import Any

from .canonical import canonical_json_bytes
from .postgres_reconciliation import (
    MAPPING_FORMAT,
    ReconciliationPlanError,
    plan_schema_v36_reconciliation,
)

REPORT_FORMAT = "sngso-postgres-mapped-row-inventory-v1"
_BATCH_MIN = 1
_BATCH_MAX = 100_000
_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_FLOAT_SPECIAL = {"NaN", "Infinity", "-Infinity"}


class PostgresRowInspectionError(RuntimeError):
    """Raised when a mapped PostgreSQL row scan cannot be trusted."""


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PostgresRowInspectionError("invalid SQL identifier in reviewed mapping")
    return '"' + value.replace('"', '""') + '"'


def _frame(tag: bytes, payload: bytes) -> bytes:
    return tag + len(payload).to_bytes(8, "big") + payload


def _encode_target_value(value: Any, conversion: str) -> bytes:
    """Encode a value fetched from PostgreSQL without lossy coercion."""
    if value is None:
        return _frame(b"N", b"")
    if not isinstance(value, str):
        raise PostgresRowInspectionError(
            "target scan expected text-cast values from PostgreSQL"
        )
    if conversion == "text_identity":
        return _frame(b"T", value.encode("utf-8", errors="strict"))
    if conversion == "integer_to_bigint":
        if not _INTEGER.fullmatch(value):
            raise PostgresRowInspectionError("PostgreSQL bigint text is not canonical")
        return _frame(b"I", value.encode("ascii"))
    if conversion == "real_to_double_precision":
        if value in _FLOAT_SPECIAL:
            raise PostgresRowInspectionError("non-finite PostgreSQL float is not import-safe")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PostgresRowInspectionError("invalid PostgreSQL float text") from exc
        if not math.isfinite(number):
            raise PostgresRowInspectionError("non-finite PostgreSQL float is not import-safe")
        return _frame(b"R", struct.pack(">d", number))
    if conversion == "blob_to_bytea":
        if len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", value):
            raise PostgresRowInspectionError("PostgreSQL bytea hex output is malformed")
        return _frame(b"B", bytes.fromhex(value))
    if conversion == "json_text_to_jsonb":
        # PostgreSQL's jsonb textual renderer is normalized by PostgreSQL itself.
        # Preserve it byte-for-byte; do not pretend it equals source JSON text.
        return _frame(b"J", value.encode("utf-8", errors="strict"))
    raise PostgresRowInspectionError("unsupported conversion in mapped-row scan")


def inspect_postgres_mapped_rows(
    connection: Any,
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
    mapping_spec: dict[str, Any],
    *,
    batch_size: int = 512,
) -> dict[str, Any]:
    """Fingerprint reviewed target relations in one REPEATABLE READ READ ONLY snapshot.

    The caller supplies the source manifest, prior catalog inventory, and
    approved mapping. The mapping must pass the structural planner. During the
    scan, schema identity, migration ledger, mapped relation/column types, and
    primary-key order are rechecked against that inventory. Target values are
    fetched as PostgreSQL text representations and encoded by declared rule.

    The report never contains primary-key values or row payloads. It always
    returns INCOMPLETE because no source-side converted digest is compared here.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not (
        _BATCH_MIN <= batch_size <= _BATCH_MAX
    ):
        raise PostgresRowInspectionError("batch_size must be an integer from 1 to 100000")
    if not isinstance(mapping_spec, dict) or mapping_spec.get("format") != MAPPING_FORMAT:
        raise PostgresRowInspectionError("approved mapping specification is required")
    try:
        plan = plan_schema_v36_reconciliation(
            source_manifest, target_inventory, mapping_spec
        )
    except (ReconciliationPlanError, KeyError, TypeError) as exc:
        raise PostgresRowInspectionError("mapping failed structural validation") from exc
    if not plan.get("structure_valid"):
        raise PostgresRowInspectionError("mapping has unresolved structural issues")

    info = getattr(connection, "info", None)
    status = getattr(getattr(info, "transaction_status", None), "name", None)
    if status != "IDLE":
        raise PostgresRowInspectionError("PostgreSQL row inspection requires an idle connection")
    transaction = getattr(connection, "transaction", None)
    cursor_factory = getattr(connection, "cursor", None)
    if not callable(transaction) or not callable(cursor_factory):
        raise TypeError("connection must provide psycopg 3 transaction() and cursor()")

    schema_name = target_inventory.get("identity", {}).get("schema_name")
    if not isinstance(schema_name, str) or not schema_name:
        raise PostgresRowInspectionError("target inventory has no active schema")
    inventory_columns = {
        (item["table_name"], item["column_name"]): item
        for item in target_inventory.get("columns", [])
    }
    inventory_tables = {item["table_name"] for item in target_inventory.get("tables", [])}
    inventory_pks = {
        item["table_name"]: list(item["columns"])
        for item in target_inventory.get("primary_keys", [])
    }

    table_reports: list[dict[str, Any]] = []
    try:
        with transaction():
            with cursor_factory() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute(
                    "SELECT current_schema(), current_setting('server_version_num')::integer"
                )
                active_schema, server_version_num = cursor.fetchone()
                if active_schema != schema_name:
                    raise PostgresRowInspectionError("target active schema changed since inventory")
                expected_server_version = target_inventory.get("identity", {}).get("server_version_num")
                if server_version_num != expected_server_version:
                    raise PostgresRowInspectionError("PostgreSQL server version changed since inventory")

                mapped_target_names = [
                    item["target_table"] for item in mapping_spec["tables"]
                ]
                cursor.execute(
                    """SELECT c.relname AS table_name, a.attname AS column_name,
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
                       WHERE n.nspname = %s AND c.relname = ANY(%s)
                         AND c.relkind IN ('r', 'p')
                         AND a.attnum > 0 AND NOT a.attisdropped
                       ORDER BY c.relname, a.attnum""",
                    (schema_name, mapped_target_names),
                )
                current_columns = {
                    (row[0], row[1]): {
                        "data_type": row[2], "nullable": row[3],
                        "default_expression": row[4], "identity_kind": row[5],
                        "generated_kind": row[6],
                    }
                    for row in cursor.fetchall()
                }
                cursor.execute(
                    """SELECT c.relname AS table_name,
                              array_agg(a.attname ORDER BY k.ord) AS columns
                       FROM pg_constraint con
                       JOIN pg_class c ON c.oid = con.conrelid
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
                            AS k(attnum, ord) ON true
                       JOIN pg_attribute a
                         ON a.attrelid = c.oid AND a.attnum = k.attnum
                       WHERE n.nspname = %s AND c.relname = ANY(%s)
                         AND con.contype = 'p'
                       GROUP BY c.relname ORDER BY c.relname""",
                    (schema_name, mapped_target_names),
                )
                current_primary_keys = {
                    row[0]: list(row[1]) for row in cursor.fetchall()
                }
                expected_mapped_columns = {
                    key for key in inventory_columns if key[0] in mapped_target_names
                }
                if set(current_columns) != expected_mapped_columns:
                    raise PostgresRowInspectionError(
                        "target mapped-table column inventory changed since inspection"
                    )
                for table_name in mapped_target_names:
                    if current_primary_keys.get(table_name) != inventory_pks.get(table_name):
                        raise PostgresRowInspectionError(
                            "target primary-key catalog changed since inventory"
                        )
                for (table_name, column_name), expected_meta in inventory_columns.items():
                    if table_name not in mapped_target_names:
                        continue
                    current_meta = current_columns.get((table_name, column_name))
                    if current_meta != {
                        key: expected_meta.get(key)
                        for key in (
                            "data_type", "nullable", "default_expression",
                            "identity_kind", "generated_kind",
                        )
                    }:
                        raise PostgresRowInspectionError(
                            "target column catalog changed since inventory"
                        )

                # Recheck the packaged migration ledger inside this same snapshot.
                cursor.execute(
                    "SELECT version, name, checksum FROM postgres_schema_migrations ORDER BY version"
                )
                from .postgres_migration_runner import _MIGRATIONS, _migration_bytes
                actual_ledger = cursor.fetchall()
                expected_ledger = [
                    (version, name, "sha256:" + hashlib.sha256(_migration_bytes(name)).hexdigest())
                    for version, name in _MIGRATIONS
                ]
                if actual_ledger != expected_ledger:
                    raise PostgresRowInspectionError("target migration ledger changed or diverged")

                for table_spec in mapping_spec["tables"]:
                    target_table = table_spec["target_table"]
                    if target_table not in inventory_tables:
                        raise PostgresRowInspectionError("mapped target relation is absent from inventory")
                    column_specs = table_spec["columns"]
                    target_pk = inventory_pks.get(target_table)
                    mapped_pk = [
                        next(
                            item["target_column"] for item in column_specs
                            if item["source_column"] == source_pk
                        )
                        for source_pk in next(
                            item["primary_key"]
                            for item in source_manifest["tables"]
                            if item["table"] == table_spec["source_table"]
                        )
                    ]
                    if target_pk != mapped_pk:
                        raise PostgresRowInspectionError("target primary-key order changed since inventory")
                    for item in column_specs:
                        meta = inventory_columns.get((target_table, item["target_column"]))
                        if meta is None:
                            raise PostgresRowInspectionError("mapped target column is absent from inventory")
                        if item["conversion"] == "text_identity" and meta["data_type"].lower() not in {
                            "text", "character varying", "character"
                        }:
                            raise PostgresRowInspectionError("target text type changed since inventory")
                        if item["conversion"] == "integer_to_bigint" and meta["data_type"].lower() != "bigint":
                            raise PostgresRowInspectionError("target bigint type changed since inventory")
                        if item["conversion"] == "real_to_double_precision" and meta["data_type"].lower() != "double precision":
                            raise PostgresRowInspectionError("target float type changed since inventory")
                        if item["conversion"] == "blob_to_bytea" and meta["data_type"].lower() != "bytea":
                            raise PostgresRowInspectionError("target bytea type changed since inventory")
                        if item["conversion"] == "json_text_to_jsonb" and meta["data_type"].lower() != "jsonb":
                            raise PostgresRowInspectionError("target jsonb type changed since inventory")

                    select_parts = []
                    for item in column_specs:
                        name = _quote_identifier(item["target_column"])
                        if item["conversion"] == "blob_to_bytea":
                            select_parts.append(f"encode({name}, 'hex') AS {_quote_identifier(item['target_column'])}")
                        else:
                            select_parts.append(f"{name}::text AS {_quote_identifier(item['target_column'])}")
                    order_parts = ", ".join(_quote_identifier(name) for name in target_pk)
                    query = (
                        f"SELECT {', '.join(select_parts)} "
                        f"FROM {_quote_identifier(schema_name)}.{_quote_identifier(target_table)} "
                        f"ORDER BY {order_parts}"
                    )
                    cursor.execute(query)
                    digest = hashlib.sha256()
                    digest.update(REPORT_FORMAT.encode("ascii") + b"\x00")
                    digest.update(canonical_json_bytes({
                        "source_table": table_spec["source_table"],
                        "target_table": target_table,
                        "columns": column_specs,
                        "primary_key": target_pk,
                    }))
                    digest.update(b"\x00")
                    row_count = 0
                    while True:
                        batch = cursor.fetchmany(batch_size)
                        if not batch:
                            break
                        for row in batch:
                            digest.update(b"ROW\x00")
                            for index, item in enumerate(column_specs):
                                digest.update(_encode_target_value(row[index], item["conversion"]))
                            row_count += 1
                    table_reports.append({
                        "source_table": table_spec["source_table"],
                        "target_table": target_table,
                        "row_count": row_count,
                        "target_row_sha256": digest.hexdigest(),
                        "source_row_count": next(
                            item["row_count"] for item in source_manifest["tables"]
                            if item["table"] == table_spec["source_table"]
                        ),
                        "row_count_matches_source": row_count == next(
                            item["row_count"] for item in source_manifest["tables"]
                            if item["table"] == table_spec["source_table"]
                        ),
                    })
    except PostgresRowInspectionError:
        raise
    except Exception as exc:
        raise PostgresRowInspectionError(
            "PostgreSQL mapped-row inspection failed; no target writes were attempted"
        ) from exc

    report = {
        "format": REPORT_FORMAT,
        "outcome": "INCOMPLETE",
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "target_inventory_sha256": target_inventory["inventory_sha256"],
        "mapping_sha256": mapping_spec["mapping_sha256"],
        "server_version_num": server_version_num,
        "table_reports": table_reports,
        "all_row_counts_match": all(item["row_count_matches_source"] for item in table_reports),
        "remaining_gates": [
            "SOURCE_SIDE_CONVERTED_ROW_CHECKSUM_COMPARISON",
            "JSONB_SEMANTIC_NORMALIZATION_PROOF",
            "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
        ],
    }
    report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    return report
