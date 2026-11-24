"""Read-only, manifest-pinned validation of SQLite TEXT values mapped to PostgreSQL jsonb."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .sqlite_export import (
    EXPECTED_SCHEMA_VERSION,
    MANIFEST_FORMAT,
    SQLiteExportError,
    _quote_identifier,
    _stream_table,
    _table_metadata,
)

PROOF_FORMAT = "sngso-sqlite-jsonb-content-proof-v1"
MAPPING_FORMAT = "sngso-sqlite-to-postgres-mapping-v1"


class SQLiteJSONValidationError(RuntimeError):
    """Raised when JSONB source validation cannot establish a trustworthy proof."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _manifest_digest(manifest: dict[str, Any]) -> str:
    base = dict(manifest)
    supplied = base.pop("manifest_sha256", None)
    actual = hashlib.sha256(canonical_json_bytes(base)).hexdigest()
    if supplied != actual:
        raise SQLiteJSONValidationError("source manifest fingerprint mismatch")
    return actual


def _mapping_digest(mapping: dict[str, Any]) -> str:
    base = dict(mapping)
    supplied = base.pop("mapping_sha256", None)
    actual = hashlib.sha256(canonical_json_bytes(base)).hexdigest()
    if supplied != actual:
        raise SQLiteJSONValidationError("mapping fingerprint mismatch")
    return actual


def validate_sqlite_jsonb_content(
    database: str | Path,
    source_manifest: dict[str, Any],
    mapping_spec: dict[str, Any],
    *,
    batch_size: int = 512,
) -> dict[str, Any]:
    """Validate every explicitly mapped JSON text cell in one read-only snapshot.

    The full source table checksums are recomputed in the same SQLite transaction
    before JSON parsing. Reports contain key digests and counts, never source values.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 100_000:
        raise SQLiteJSONValidationError("batch_size must be an integer from 1 to 100000")
    if not isinstance(source_manifest, dict) or source_manifest.get("format") != MANIFEST_FORMAT:
        raise SQLiteJSONValidationError("unsupported source manifest")
    manifest_digest = _manifest_digest(source_manifest)
    if source_manifest.get("source", {}).get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise SQLiteJSONValidationError("source manifest is not schema version 36")
    if not isinstance(mapping_spec, dict) or mapping_spec.get("format") != MAPPING_FORMAT:
        raise SQLiteJSONValidationError("unsupported mapping specification")
    mapping_digest = _mapping_digest(mapping_spec)
    if mapping_spec.get("source_manifest_sha256") != manifest_digest:
        raise SQLiteJSONValidationError("mapping is not pinned to this source manifest")
    if mapping_spec.get("source_schema_sha256") != source_manifest["source"].get("schema_sha256"):
        raise SQLiteJSONValidationError("mapping source schema fingerprint mismatch")
    review = mapping_spec.get("review")
    if not isinstance(review, dict) or review.get("status") != "approved" or any(
        not isinstance(review.get(k), str) or not review[k].strip()
        for k in ("reviewer", "reviewed_at", "reference")
    ):
        raise SQLiteJSONValidationError("mapping review approval is missing")

    selected: dict[str, list[str]] = {}
    for table_spec in mapping_spec.get("tables", []):
        if not isinstance(table_spec, dict):
            raise SQLiteJSONValidationError("invalid table mapping")
        table = table_spec.get("source_table")
        for column_spec in table_spec.get("columns", []):
            if not isinstance(column_spec, dict):
                raise SQLiteJSONValidationError("invalid column mapping")
            if column_spec.get("conversion") == "json_text_to_jsonb":
                column = column_spec.get("source_column")
                if not isinstance(table, str) or not isinstance(column, str):
                    raise SQLiteJSONValidationError("invalid JSONB source mapping")
                selected.setdefault(table, []).append(column)
    if not selected:
        raise SQLiteJSONValidationError("mapping contains no json_text_to_jsonb columns")
    if any(len(cols) != len(set(cols)) for cols in selected.values()):
        raise SQLiteJSONValidationError("duplicate JSONB source column mapping")

    manifest_tables = {item["table"]: item for item in source_manifest.get("tables", [])}
    if set(selected) - set(manifest_tables):
        raise SQLiteJSONValidationError("JSONB mapping references an unknown source table")
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise SQLiteJSONValidationError("source database does not exist")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise SQLiteJSONValidationError("could not open source database read-only") from exc

    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != EXPECTED_SCHEMA_VERSION:
            raise SQLiteJSONValidationError("live source schema version changed")
        for table_name, expected in manifest_tables.items():
            metadata = _table_metadata(connection, table_name)
            actual = _stream_table(connection, metadata, batch_size=batch_size)
            if actual["row_count"] != expected.get("row_count") or actual["row_sha256"] != expected.get("row_sha256"):
                raise SQLiteJSONValidationError(f"source table differs from pinned manifest: {table_name}")
            if hashlib.sha256(canonical_json_bytes(metadata)).hexdigest() != expected.get("schema_sha256"):
                raise SQLiteJSONValidationError(f"source table schema differs from pinned manifest: {table_name}")

        table_proofs = []
        for table_name in sorted(selected):
            metadata = manifest_tables[table_name]
            column_names = {item["name"] for item in metadata["columns"]}
            columns = sorted(selected[table_name])
            if set(columns) - column_names:
                raise SQLiteJSONValidationError(f"JSONB mapping references unknown column in {table_name}")
            key_columns = metadata["primary_key"]
            select_names = key_columns + columns
            select_sql = ", ".join(_quote_identifier(name) for name in select_names)
            order_sql = ", ".join(_quote_identifier(name) for name in key_columns)
            cursor = connection.execute(
                f"SELECT {select_sql} FROM {_quote_identifier(table_name)} ORDER BY {order_sql}"
            )
            stats = {
                column: {"validated": 0, "null": 0, "invalid": 0, "content_sha256": hashlib.sha256()}
                for column in columns
            }
            invalid_key_digests = {column: hashlib.sha256() for column in columns}
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    key_payload = canonical_json_bytes([row[key] for key in key_columns])
                    for column in columns:
                        value = row[column]
                        state = stats[column]
                        if value is None:
                            state["null"] += 1
                            state["content_sha256"].update(b"NULL\\x00")
                            continue
                        if not isinstance(value, str):
                            state["invalid"] += 1
                            invalid_key_digests[column].update(key_payload + b"\\x00")
                            continue
                        try:
                            parsed = json.loads(value, parse_constant=_reject_constant)
                            canonical = canonical_json_bytes(parsed)
                            canonical.decode("utf-8", errors="strict")
                        except (ValueError, TypeError, UnicodeError, RecursionError):
                            state["invalid"] += 1
                            invalid_key_digests[column].update(key_payload + b"\\x00")
                            continue
                        state["validated"] += 1
                        state["content_sha256"].update(len(canonical).to_bytes(8, "big") + canonical)
            rendered_columns = []
            for column in columns:
                state = stats[column]
                rendered_columns.append({
                    "source_column": column,
                    "validated_non_null": state["validated"],
                    "null": state["null"],
                    "invalid": state["invalid"],
                    "canonical_content_sha256": state["content_sha256"].hexdigest(),
                    "invalid_primary_key_set_sha256": invalid_key_digests[column].hexdigest(),
                })
            table_proofs.append({"source_table": table_name, "columns": rendered_columns})
        proof = {
            "format": PROOF_FORMAT,
            "outcome": "VALID" if all(
                item["invalid"] == 0
                for table in table_proofs for item in table["columns"]
            ) else "INVALID",
            "source_manifest_sha256": manifest_digest,
            "mapping_sha256": mapping_digest,
            "table_proofs": table_proofs,
        }
        proof["proof_sha256"] = hashlib.sha256(canonical_json_bytes(proof)).hexdigest()
        return proof
    except sqlite3.Error as exc:
        raise SQLiteJSONValidationError("SQLite JSONB content validation failed") from exc
    finally:
        connection.close()
