"""Read-only PostgreSQL-native JSONB semantic reconciliation for schema-v36 sources.

SQLite JSON text is cast by PostgreSQL itself, then compared row-by-row with the
target jsonb value in one repeatable-read snapshot. No importer or target writes
are involved.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .postgres_reconciliation import (
    MAPPING_FORMAT,
    ReconciliationPlanError,
    plan_schema_v36_reconciliation,
)
from .postgres_row_inspection import (
    PostgresRowInspectionError,
    _encode_target_value,
    _frame,
    _quote_identifier as quote_pg,
)
from .sqlite_converted_row_inspection import (
    SQLiteConvertedRowInspectionError,
    _encode_source_value,
)
from .sqlite_export import (
    EXPECTED_SCHEMA_VERSION,
    MANIFEST_FORMAT,
    _quote_identifier as quote_sqlite,
    _stream_table,
    _table_metadata,
)
from .sqlite_json_validation import (
    _manifest_digest,
    _mapping_digest,
)

REPORT_FORMAT = "sngso-postgres-jsonb-semantic-proof-v1"


class PostgresJSONBSemanticError(RuntimeError):
    """Raised when PostgreSQL-native JSONB equivalence cannot be proven."""


def prove_sqlite_jsonb_semantic_equivalence(
    database: str | Path,
    connection: Any,
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
    mapping_spec: dict[str, Any],
    *,
    batch_size: int = 512,
) -> dict[str, Any]:
    """Compare mapped SQLite JSON text with actual PostgreSQL jsonb values.

    PostgreSQL performs both the source cast and target read. The source and
    target rows are paired by converted primary-key values, not merely by row
    position. Target reads run in a single REPEATABLE READ READ ONLY snapshot.
    The source is opened read-only and revalidated against its schema-v36
    manifest in the same SQLite read transaction.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 100_000:
        raise PostgresJSONBSemanticError("batch_size must be an integer from 1 to 100000")
    if not isinstance(source_manifest, dict) or source_manifest.get("format") != MANIFEST_FORMAT:
        raise PostgresJSONBSemanticError("unsupported source manifest")
    if not isinstance(target_inventory, dict) or not isinstance(mapping_spec, dict):
        raise PostgresJSONBSemanticError("source manifest, target inventory and mapping are required")
    try:
        manifest_sha = _manifest_digest(source_manifest)
        mapping_sha = _mapping_digest(mapping_spec)
        if source_manifest.get("source", {}).get("schema_version") != EXPECTED_SCHEMA_VERSION:
            raise PostgresJSONBSemanticError("source manifest is not schema version 36")
        if mapping_spec.get("format") != MAPPING_FORMAT:
            raise PostgresJSONBSemanticError("unsupported mapping specification")
        if mapping_spec.get("source_manifest_sha256") != manifest_sha:
            raise PostgresJSONBSemanticError("mapping is not pinned to this source manifest")
        if mapping_spec.get("source_schema_sha256") != source_manifest["source"].get("schema_sha256"):
            raise PostgresJSONBSemanticError("mapping source schema fingerprint mismatch")
        review = mapping_spec.get("review")
        if not isinstance(review, dict) or review.get("status") != "approved" or any(
            not isinstance(review.get(key), str) or not review[key].strip()
            for key in ("reviewer", "reviewed_at", "reference")
        ):
            raise PostgresJSONBSemanticError("mapping review approval is missing")
        plan = plan_schema_v36_reconciliation(source_manifest, target_inventory, mapping_spec)
        if not plan.get("structure_valid"):
            raise PostgresJSONBSemanticError("mapping failed structural validation")
    except (ReconciliationPlanError, KeyError, TypeError) as exc:
        raise PostgresJSONBSemanticError("source, target or mapping fingerprints are invalid") from exc

    status = getattr(getattr(getattr(connection, "info", None), "transaction_status", None), "name", None)
    if status != "IDLE":
        raise PostgresJSONBSemanticError("JSONB semantic proof requires an idle PostgreSQL connection")
    if not callable(getattr(connection, "transaction", None)) or not callable(getattr(connection, "cursor", None)):
        raise TypeError("connection must provide psycopg 3 transaction() and cursor()")

    source_path = Path(database).expanduser().resolve()
    if not source_path.is_file():
        raise PostgresJSONBSemanticError("source database does not exist")
    try:
        sqlite = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise PostgresJSONBSemanticError("could not open source database read-only") from exc

    source_tables = {item["table"]: item for item in source_manifest.get("tables", [])}
    target_columns = {
        (item["table_name"], item["column_name"]): item
        for item in target_inventory.get("columns", [])
    }
    target_pks = {
        item["table_name"]: list(item["columns"])
        for item in target_inventory.get("primary_keys", [])
    }
    target_schema = target_inventory.get("identity", {}).get("schema_name")
    if not isinstance(target_schema, str) or not target_schema:
        sqlite.close()
        raise PostgresJSONBSemanticError("target inventory has no schema identity")

    table_reports = []
    server_version_num = None
    try:
        sqlite.row_factory = sqlite3.Row
        sqlite.execute("PRAGMA query_only = ON")
        sqlite.execute("BEGIN")
        if int(sqlite.execute("PRAGMA user_version").fetchone()[0]) != EXPECTED_SCHEMA_VERSION:
            raise PostgresJSONBSemanticError("live SQLite schema version changed")
        for table_name, expected in source_tables.items():
            metadata = _table_metadata(sqlite, table_name)
            actual = _stream_table(sqlite, metadata, batch_size=batch_size)
            if actual["row_count"] != expected.get("row_count") or actual["row_sha256"] != expected.get("row_sha256"):
                raise PostgresJSONBSemanticError(f"source table differs from manifest: {table_name}")
            if hashlib.sha256(canonical_json_bytes(metadata)).hexdigest() != expected.get("schema_sha256"):
                raise PostgresJSONBSemanticError(f"source table schema differs from manifest: {table_name}")

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SELECT current_schema(), current_setting('server_version_num')::integer")
                active_schema, server_version_num = cursor.fetchone()
                if active_schema != target_schema or server_version_num != target_inventory.get("identity", {}).get("server_version_num"):
                    raise PostgresJSONBSemanticError("PostgreSQL target identity changed since inventory")

                for table_index, table_spec in enumerate(mapping_spec["tables"]):
                    source_table = table_spec["source_table"]
                    target_table = table_spec["target_table"]
                    columns = table_spec["columns"]
                    json_columns = [item for item in columns if item["conversion"] == "json_text_to_jsonb"]
                    if not json_columns:
                        continue
                    source_meta = source_tables[source_table]
                    source_pk = list(source_meta["primary_key"])
                    source_to_target = {item["source_column"]: item for item in columns}
                    if any(key not in source_to_target for key in source_pk):
                        raise PostgresJSONBSemanticError("mapped primary key is incomplete")
                    target_pk = [source_to_target[key]["target_column"] for key in source_pk]
                    if target_pks.get(target_table) != target_pk:
                        raise PostgresJSONBSemanticError("target primary-key order differs from approved mapping")

                    for item in json_columns:
                        target_meta = target_columns.get((target_table, item["target_column"]))
                        if target_meta is None or target_meta.get("data_type", "").lower() != "jsonb":
                            raise PostgresJSONBSemanticError("mapped target column is not PostgreSQL jsonb")

                    source_select = source_pk + [item["source_column"] for item in json_columns]
                    source_sql = (
                        f"SELECT {', '.join(quote_sqlite(name) for name in source_select)} "
                        f"FROM {quote_sqlite(source_table)} "
                        f"ORDER BY {', '.join(quote_sqlite(name) for name in source_pk)}"
                    )
                    source_cursor = sqlite.execute(source_sql)

                    target_select = []
                    for key in target_pk:
                        target_select.append(f"{quote_pg(key)}::text")
                    for item in json_columns:
                        target_select.append(f"{quote_pg(item['target_column'])}::text")
                    order_sql = []
                    for key in target_pk:
                        conversion = next(x["conversion"] for x in columns if x["target_column"] == key)
                        order_sql.append(
                            f'{quote_pg(key)} COLLATE "C"' if conversion == "text_identity" else quote_pg(key)
                        )
                    target_query = (
                        f"SELECT {', '.join(target_select)} "
                        f"FROM {quote_pg(target_schema)}.{quote_pg(target_table)} "
                        f"ORDER BY {', '.join(order_sql)}"
                    )
                    with connection.cursor(name=f"sngso_jsonb_semantic_{table_index}") as target_cursor:
                        target_cursor.execute(target_query)
                        column_states = {
                            item["source_column"]: {
                                "digest": hashlib.sha256(),
                                "matched": 0,
                                "null": 0,
                                "mismatched": 0,
                            }
                            for item in json_columns
                        }
                        for item in json_columns:
                            column_states[item["source_column"]]["digest"].update(
                                canonical_json_bytes({
                                    "source_table": source_table,
                                    "target_table": target_table,
                                    "source_column": item["source_column"],
                                    "target_column": item["target_column"],
                                    "conversion": "json_text_to_jsonb",
                                }) + b"\x00"
                            )
                        row_count = 0
                        while True:
                            source_batch = source_cursor.fetchmany(batch_size)
                            if not source_batch:
                                break
                            target_batch = target_cursor.fetchmany(len(source_batch))
                            if len(target_batch) != len(source_batch):
                                raise PostgresJSONBSemanticError("source and target row counts differ")
                            normalized_by_column = {}
                            for json_index, item in enumerate(json_columns):
                                source_values = [row[item["source_column"]] for row in source_batch]
                                with connection.cursor() as normalize_cursor:
                                    normalize_cursor.execute(
                                        """SELECT value::jsonb::text
                                           FROM unnest(%s::text[]) WITH ORDINALITY AS v(value, ordinal)
                                           ORDER BY ordinal""",
                                        (source_values,),
                                    )
                                    normalized_by_column[item["source_column"]] = [
                                        row[0] for row in normalize_cursor.fetchall()
                                    ]
                            for source_row, target_row_index in zip(source_batch, range(len(target_batch))):
                                target_row = target_batch[target_row_index]
                                for key_index, source_key in enumerate(source_pk):
                                    mapping = source_to_target[source_key]
                                    source_encoded = _encode_source_value(source_row[source_key], mapping["conversion"])
                                    target_encoded = _encode_target_value(target_row[key_index], mapping["conversion"])
                                    if source_encoded != target_encoded:
                                        raise PostgresJSONBSemanticError("source and target primary-key rows do not align")
                                for json_index, item in enumerate(json_columns):
                                    source_value = source_row[item["source_column"]]
                                    normalized = normalized_by_column[item["source_column"]][target_row_index]
                                    target_value = target_row[len(source_pk) + json_index]
                                    state = column_states[item["source_column"]]
                                    if normalized != target_value:
                                        state["mismatched"] += 1
                                    else:
                                        state["matched"] += 1
                                    if source_value is None:
                                        state["null"] += 1
                                        encoded = _frame(b"N", b"")
                                    else:
                                        if not isinstance(source_value, str) or normalized is None:
                                            raise PostgresJSONBSemanticError("non-null JSON source did not normalize to JSONB text")
                                        encoded = _frame(b"J", normalized.encode("utf-8", errors="strict"))
                                    state["digest"].update(b"VALUE\x00" + encoded)
                                row_count += 1
                        extra_target = target_cursor.fetchmany(1)
                        if extra_target:
                            raise PostgresJSONBSemanticError("target contains extra mapped rows")
                    table_reports.append({
                        "source_table": source_table,
                        "target_table": target_table,
                        "row_count": row_count,
                        "columns": [
                            {
                                "source_column": item["source_column"],
                                "target_column": item["target_column"],
                                "matched": column_states[item["source_column"]]["matched"],
                                "null": column_states[item["source_column"]]["null"],
                                "mismatched": column_states[item["source_column"]]["mismatched"],
                                "normalized_source_sha256": column_states[item["source_column"]]["digest"].hexdigest(),
                            }
                            for item in json_columns
                        ],
                    })
        report = {
            "format": REPORT_FORMAT,
            "outcome": "VALID" if all(
                item["mismatched"] == 0
                for table in table_reports for item in table["columns"]
            ) else "INVALID",
            "source_manifest_sha256": manifest_sha,
            "target_inventory_sha256": target_inventory.get("inventory_sha256"),
            "mapping_sha256": mapping_sha,
            "server_version_num": server_version_num,
            "table_reports": table_reports,
            "read_only": True,
        }
        report["proof_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
        return report
    except PostgresJSONBSemanticError:
        raise
    except (sqlite3.Error, PostgresRowInspectionError, SQLiteConvertedRowInspectionError) as exc:
        raise PostgresJSONBSemanticError("JSONB semantic reconciliation failed closed") from exc
    except Exception as exc:
        raise PostgresJSONBSemanticError("JSONB semantic reconciliation failed closed") from exc
    finally:
        sqlite.close()
