"""Read-only SQLite converted-row evidence for schema-v36 PostgreSQL reconciliation.

The source scanner uses the same framed type encodings as the PostgreSQL
mapped-row scanner. It deliberately excludes JSONB columns from the comparable
row digest: PostgreSQL jsonb normalizes JSON text, so raw source text is not a
valid semantic checksum for the target representation.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .postgres_reconciliation import MAPPING_FORMAT, CONVERSION_SOURCE_CLASSES
from .postgres_row_inspection import REPORT_FORMAT, _frame
from .sqlite_export import (
    EXPECTED_SCHEMA_VERSION,
    MANIFEST_FORMAT,
    _quote_identifier,
    _stream_table,
    _table_metadata,
)
from .sqlite_json_validation import _manifest_digest, _mapping_digest

SOURCE_REPORT_FORMAT = "sngso-sqlite-converted-row-proof-v1"


class SQLiteConvertedRowInspectionError(RuntimeError):
    """Raised when converted source-row evidence cannot be established."""


def _encode_source_value(value: Any, conversion: str) -> bytes:
    if value is None:
        return _frame(b"N", b"")
    if conversion == "text_identity":
        if not isinstance(value, str):
            raise SQLiteConvertedRowInspectionError("source value is not SQLite TEXT")
        if "\x00" in value:
            raise SQLiteConvertedRowInspectionError("PostgreSQL text cannot represent U+0000")
        return _frame(b"T", value.encode("utf-8", errors="strict"))
    if conversion == "integer_to_bigint":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SQLiteConvertedRowInspectionError("source value is not SQLite INTEGER")
        if not -(2**63) <= value <= (2**63 - 1):
            raise SQLiteConvertedRowInspectionError("source integer exceeds PostgreSQL bigint range")
        return _frame(b"I", str(value).encode("ascii"))
    if conversion == "real_to_double_precision":
        if not isinstance(value, float) or not math.isfinite(value):
            raise SQLiteConvertedRowInspectionError("source value is not a finite SQLite REAL")
        return _frame(b"R", struct.pack(">d", value))
    if conversion == "blob_to_bytea":
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise SQLiteConvertedRowInspectionError("source value is not SQLite BLOB")
        return _frame(b"B", bytes(value))
    if conversion == "json_text_to_jsonb":
        if not isinstance(value, str):
            raise SQLiteConvertedRowInspectionError("JSONB source value is not SQLite TEXT")
        # This digest records source text only. It is intentionally incomparable
        # with PostgreSQL jsonb::text until semantic normalization is proven.
        return _frame(b"J", value.encode("utf-8", errors="strict"))
    raise SQLiteConvertedRowInspectionError("unsupported source conversion")


def inspect_sqlite_converted_rows(
    database: str | Path,
    source_manifest: dict[str, Any],
    mapping_spec: dict[str, Any],
    *,
    batch_size: int = 512,
) -> dict[str, Any]:
    """Fingerprint mapped SQLite rows under the reviewed PostgreSQL conversion rules."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 100_000:
        raise SQLiteConvertedRowInspectionError("batch_size must be an integer from 1 to 100000")
    if not isinstance(source_manifest, dict) or source_manifest.get("format") != MANIFEST_FORMAT:
        raise SQLiteConvertedRowInspectionError("unsupported SQLite source manifest")
    try:
        manifest_digest = _manifest_digest(source_manifest)
        mapping_digest = _mapping_digest(mapping_spec)
    except Exception as exc:
        raise SQLiteConvertedRowInspectionError("source manifest or mapping fingerprint mismatch") from exc
    if source_manifest.get("source", {}).get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise SQLiteConvertedRowInspectionError("source manifest is not schema version 36")
    if not isinstance(mapping_spec, dict) or mapping_spec.get("format") != MAPPING_FORMAT:
        raise SQLiteConvertedRowInspectionError("unsupported mapping format")
    if mapping_spec.get("source_manifest_sha256") != manifest_digest:
        raise SQLiteConvertedRowInspectionError("mapping does not pin this source manifest")
    if mapping_spec.get("source_schema_sha256") != source_manifest["source"].get("schema_sha256"):
        raise SQLiteConvertedRowInspectionError("mapping does not pin this source schema")
    review = mapping_spec.get("review")
    if not isinstance(review, dict) or review.get("status") != "approved" or any(
        not isinstance(review.get(key), str) or not review[key].strip()
        for key in ("reviewer", "reviewed_at", "reference")
    ):
        raise SQLiteConvertedRowInspectionError("mapping review is not approved")
    if not isinstance(mapping_spec.get("tables"), list):
        raise SQLiteConvertedRowInspectionError("mapping table list is invalid")

    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise SQLiteConvertedRowInspectionError("SQLite source database does not exist")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise SQLiteConvertedRowInspectionError("could not open SQLite source read-only") from exc

    table_reports: list[dict[str, Any]] = []
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != EXPECTED_SCHEMA_VERSION:
            raise SQLiteConvertedRowInspectionError("SQLite source schema version changed")
        migrations = [
            int(row[0]) for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER)"
            )
        ]
        if migrations != list(range(1, EXPECTED_SCHEMA_VERSION + 1)):
            raise SQLiteConvertedRowInspectionError("SQLite migration history is not contiguous")
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise SQLiteConvertedRowInspectionError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise SQLiteConvertedRowInspectionError("SQLite source has foreign-key violations")

        objects = connection.execute(
            """SELECT type, name, tbl_name, sql FROM sqlite_master
               WHERE type IN ('table', 'index', 'trigger', 'view')
                 AND name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        object_inventory = [
            {"type": row[0], "name": row[1], "table_name": row[2], "sql": row[3]}
            for row in objects
        ]
        object_counts = {
            kind: sum(item["type"] == kind for item in object_inventory)
            for kind in ("table", "index", "trigger", "view")
        }
        schema_digest = hashlib.sha256(canonical_json_bytes(object_inventory)).hexdigest()
        if schema_digest != source_manifest["source"].get("schema_sha256"):
            raise SQLiteConvertedRowInspectionError(
                "SQLite global schema object inventory changed since manifest"
            )

        source_table_list = source_manifest.get("tables", [])
        source_tables = {item["table"]: item for item in source_table_list}
        if len(source_tables) != len(source_table_list):
            raise SQLiteConvertedRowInspectionError("source manifest contains duplicate tables")
        actual_table_names = [
            row[0] for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                   ORDER BY name"""
            )
        ]
        if actual_table_names != sorted(source_tables):
            raise SQLiteConvertedRowInspectionError(
                "SQLite table inventory differs from source manifest"
            )
        if object_counts != source_manifest["source"].get("object_counts"):
            raise SQLiteConvertedRowInspectionError(
                "SQLite schema object counts differ from source manifest"
            )
        mapped_source_names = [
            item.get("source_table") for item in mapping_spec["tables"]
            if isinstance(item, dict)
        ]
        mapped_target_names = [
            item.get("target_table") for item in mapping_spec["tables"]
            if isinstance(item, dict)
        ]
        if (
            len(mapped_source_names) != len(mapping_spec["tables"])
            or len(set(mapped_source_names)) != len(mapped_source_names)
            or len(set(mapped_target_names)) != len(mapped_target_names)
            or set(mapped_source_names) != set(source_tables)
        ):
            raise SQLiteConvertedRowInspectionError(
                "mapping must cover each source table exactly once"
            )
        for table_spec in mapping_spec["tables"]:
            if not isinstance(table_spec, dict):
                raise SQLiteConvertedRowInspectionError("table mapping entry is invalid")
            source_name = table_spec.get("source_table")
            manifest_table = source_tables.get(source_name)
            if manifest_table is None:
                raise SQLiteConvertedRowInspectionError("mapped source table is absent from manifest")
            metadata = _table_metadata(connection, source_name)
            schema_digest = hashlib.sha256(canonical_json_bytes(metadata)).hexdigest()
            if schema_digest != manifest_table.get("schema_sha256"):
                raise SQLiteConvertedRowInspectionError("mapped source table schema changed")
            snapshot = _stream_table(connection, metadata, batch_size=batch_size)
            for key in ("row_count", "row_sha256", "storage_class_counts"):
                if snapshot.get(key) != manifest_table.get(key):
                    raise SQLiteConvertedRowInspectionError("mapped source rows changed since manifest")

            column_specs = table_spec.get("columns")
            if not isinstance(column_specs, list) or not column_specs:
                raise SQLiteConvertedRowInspectionError("mapped source columns are invalid")
            if any(not isinstance(item, dict) for item in column_specs):
                raise SQLiteConvertedRowInspectionError("column mapping entry is invalid")
            source_columns = [item.get("source_column") for item in column_specs]
            target_columns = [item.get("target_column") for item in column_specs]
            known_source_columns = {item["name"] for item in metadata["columns"]}
            if (
                len(set(source_columns)) != len(source_columns)
                or len(set(target_columns)) != len(target_columns)
                or set(source_columns) != known_source_columns
            ):
                raise SQLiteConvertedRowInspectionError(
                    "mapping must cover each source column exactly once"
                )
            storage_counts = manifest_table.get("storage_class_counts", {})
            if not isinstance(storage_counts, dict):
                raise SQLiteConvertedRowInspectionError("source storage-class inventory is invalid")
            expected_storage_classes = {"null", "integer", "real", "text", "blob"}
            for item in column_specs:
                conversion = item.get("conversion")
                source_column = item.get("source_column")
                allowed = CONVERSION_SOURCE_CLASSES.get(conversion)
                counts = storage_counts.get(source_column)
                if (
                    allowed is None or not isinstance(counts, dict)
                    or set(counts) != expected_storage_classes
                    or any(
                        not isinstance(count, int) or isinstance(count, bool) or count < 0
                        for count in counts.values()
                    )
                    or sum(counts.values()) != manifest_table["row_count"]
                ):
                    raise SQLiteConvertedRowInspectionError(
                        "source conversion or storage-class inventory is unsupported"
                    )
                if any(count for kind, count in counts.items() if kind not in allowed):
                    raise SQLiteConvertedRowInspectionError(
                        "source storage class is incompatible with reviewed conversion"
                    )

            # PostgreSQL text primary keys are ordered with the C collation to
            # match SQLite's default BINARY byte ordering. Refuse a source PK
            # that declares a different collation until it is explicitly mapped.
            pk_conversion = {
                item["source_column"]: item["conversion"] for item in column_specs
            }
            text_pk_columns = [
                name for name in metadata["primary_key"]
                if pk_conversion[name] == "text_identity"
            ]
            if text_pk_columns:
                index_rows = connection.execute(
                    f"PRAGMA index_list({_quote_identifier(source_name)})"
                ).fetchall()
                pk_index = next((row[1] for row in index_rows if row[3] == "pk"), None)
                if pk_index is None:
                    raise SQLiteConvertedRowInspectionError(
                        "text primary-key collation cannot be verified"
                    )
                index_columns = connection.execute(
                    f"PRAGMA index_xinfo({_quote_identifier(pk_index)})"
                ).fetchall()
                collations = {
                    row[2]: row[4] for row in index_columns
                    if row[5] and row[2] is not None
                }
                if any(collations.get(name, "BINARY").upper() != "BINARY" for name in text_pk_columns):
                    raise SQLiteConvertedRowInspectionError(
                        "non-BINARY SQLite text primary-key collation is unsupported"
                    )
            order_by = ", ".join(_quote_identifier(name) for name in metadata["primary_key"])
            selected = ", ".join(_quote_identifier(name) for name in source_columns)
            cursor = connection.execute(
                f"SELECT {selected} FROM {_quote_identifier(source_name)} ORDER BY {order_by}"
            )

            column_digests = {}
            for item in column_specs:
                column_digest = hashlib.sha256()
                column_digest.update(canonical_json_bytes({
                    "source_table": source_name,
                    "target_table": table_spec["target_table"],
                    "source_column": item["source_column"],
                    "target_column": item["target_column"],
                    "conversion": item["conversion"],
                }))
                column_digest.update(b"\x00")
                column_digests[item["target_column"]] = column_digest
            non_json_columns = [
                item for item in column_specs
                if item["conversion"] != "json_text_to_jsonb"
            ]
            target_primary_key = [
                next(
                    item["target_column"] for item in column_specs
                    if item["source_column"] == source_pk
                )
                for source_pk in metadata["primary_key"]
            ]
            non_json_digest = hashlib.sha256()
            non_json_digest.update(canonical_json_bytes({
                "source_table": source_name,
                "target_table": table_spec["target_table"],
                "columns": non_json_columns,
                "primary_key": target_primary_key,
            }))
            non_json_digest.update(b"\x00")
            source_text_digests = {
                item["target_column"]: hashlib.sha256()
                for item in column_specs
                if item["conversion"] == "json_text_to_jsonb"
            }
            row_count = 0
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    non_json_digest.update(b"ROW\x00")
                    for index, item in enumerate(column_specs):
                        value = row[index]
                        encoded = _encode_source_value(value, item["conversion"])
                        column_digests[item["target_column"]].update(b"VALUE\x00" + encoded)
                        if item["conversion"] == "json_text_to_jsonb":
                            if value is None:
                                source_text_digests[item["target_column"]].update(b"N")
                            else:
                                raw = value.encode("utf-8")
                                source_text_digests[item["target_column"]].update(
                                    len(raw).to_bytes(8, "big") + raw
                                )
                        else:
                            non_json_digest.update(encoded)
                    row_count += 1
            table_reports.append({
                "source_table": source_name,
                "target_table": table_spec["target_table"],
                "row_count": row_count,
                "source_row_count": manifest_table["row_count"],
                "row_count_matches_manifest": row_count == manifest_table["row_count"],
                "column_sha256": {
                    name: digest.hexdigest() for name, digest in column_digests.items()
                },
                "non_json_row_sha256": non_json_digest.hexdigest(),
                "jsonb_source_text_sha256": {
                    name: digest.hexdigest()
                    for name, digest in source_text_digests.items()
                },
                "jsonb_columns": sorted(source_text_digests),
            })
    except SQLiteConvertedRowInspectionError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise SQLiteConvertedRowInspectionError(
            "SQLite converted-row inspection failed; source was opened read-only"
        ) from exc
    finally:
        connection.close()

    report = {
        "format": SOURCE_REPORT_FORMAT,
        "outcome": "INCOMPLETE",
        "source_manifest_sha256": manifest_digest,
        "mapping_sha256": mapping_digest,
        "table_reports": table_reports,
        "all_row_counts_match_manifest": all(
            item["row_count_matches_manifest"] for item in table_reports
        ),
        "remaining_gates": [
            "TARGET_NON_JSON_ROW_CHECKSUM_COMPARISON",
            "JSONB_SEMANTIC_NORMALIZATION_PROOF",
            "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
        ],
    }
    report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    return report


def compare_converted_row_reports(
    source_report: dict[str, Any],
    target_report: dict[str, Any],
) -> dict[str, Any]:
    """Compare non-JSON converted-row digests without promoting overall readiness."""
    if source_report.get("format") != SOURCE_REPORT_FORMAT:
        raise SQLiteConvertedRowInspectionError("unsupported source converted-row report")
    if target_report.get("format") != REPORT_FORMAT:
        raise SQLiteConvertedRowInspectionError("unsupported PostgreSQL target-row report")
    for report, field in (
        (source_report, "source_report_sha256"),
        (target_report, "target_report_sha256"),
    ):
        body = dict(report)
        supplied = body.pop("report_sha256", None)
        actual = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        if supplied != actual:
            raise SQLiteConvertedRowInspectionError("row evidence report fingerprint mismatch")
    for field in ("source_manifest_sha256", "mapping_sha256"):
        if source_report.get(field) != target_report.get(field):
            raise SQLiteConvertedRowInspectionError(f"{field} mismatch between row reports")
    source_tables = {item["source_table"]: item for item in source_report.get("table_reports", [])}
    target_tables = {item["source_table"]: item for item in target_report.get("table_reports", [])}
    if set(source_tables) != set(target_tables):
        raise SQLiteConvertedRowInspectionError("source and target report table sets differ")

    comparisons = []
    issues = []
    for table_name in sorted(source_tables):
        source = source_tables[table_name]
        target = target_tables[table_name]
        row_count_match = (
            source["row_count"] == target["row_count"]
            and source["row_count_matches_manifest"]
            and target["row_count_matches_source"]
        )
        if not row_count_match:
            issues.append({"code": "ROW_COUNT_MISMATCH", "source_table": table_name})
        source_non_json = source["non_json_row_sha256"]
        target_non_json = target["non_json_row_sha256"]
        non_json_match = source_non_json == target_non_json
        if not non_json_match:
            issues.append({
                "code": "NON_JSON_ROW_CHECKSUM_MISMATCH",
                "source_table": table_name,
                "source_sha256": source_non_json,
                "target_sha256": target_non_json,
            })
        source_jsonb = set(source.get("jsonb_columns", []))
        target_jsonb = set(target.get("jsonb_columns", []))
        if source_jsonb != target_jsonb:
            issues.append({"code": "JSONB_COLUMN_SET_MISMATCH", "source_table": table_name})
        source_comparable_columns = set(source["column_sha256"]) - source_jsonb
        target_comparable_columns = set(target["column_sha256"]) - target_jsonb
        if source_comparable_columns != target_comparable_columns:
            issues.append({"code": "MAPPED_COLUMN_SET_MISMATCH", "source_table": table_name})
        for column_name, source_digest in source["column_sha256"].items():
            if column_name in source_jsonb:
                continue
            if target["column_sha256"].get(column_name) != source_digest:
                issues.append({
                    "code": "COLUMN_CHECKSUM_MISMATCH",
                    "source_table": table_name,
                    "target_column": column_name,
                    "source_sha256": source_digest,
                    "target_sha256": target["column_sha256"].get(column_name),
                })
        comparisons.append({
            "source_table": table_name,
            "row_count_match": row_count_match,
            "non_json_row_checksum_match": non_json_match,
            "jsonb_columns_pending": sorted(source_jsonb),
        })
    return {
        "format": "sngso-converted-row-comparison-v1",
        "outcome": "INCOMPLETE",
        "source_manifest_sha256": source_report["source_manifest_sha256"],
        "mapping_sha256": source_report["mapping_sha256"],
        "source_report_sha256": source_report["report_sha256"],
        "target_report_sha256": target_report["report_sha256"],
        "non_json_values_match": not issues,
        "comparisons": comparisons,
        "issues": issues,
        "remaining_gates": [
            "JSONB_SEMANTIC_NORMALIZATION_PROOF",
            "AUDIT_CHAIN_IMPORT_AND_VERIFICATION",
        ],
    }
