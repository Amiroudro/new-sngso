"""Deterministic, read-only SQLite table inventory and row checksums.

The encoder preserves SQLite storage classes and streams rows in SQLite primary-key
order. It deliberately refuses tables without a declared primary key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .canonical import canonical_json_bytes

MANIFEST_FORMAT = "sngso-registry-import-manifest-v1"
CHECKSUM_FORMAT = "sngso-sqlite-row-stream-v1"
EXPECTED_SCHEMA_VERSION = 36


class SQLiteExportError(RuntimeError):
    """Raised when a source database cannot be safely fingerprinted."""


def _frame(tag: bytes, payload: bytes) -> bytes:
    if len(tag) != 1:
        raise ValueError("encoding tag must be one byte")
    return tag + len(payload).to_bytes(8, "big") + payload


def encode_sqlite_value(value: Any) -> bytes:
    """Encode one SQLite value with an explicit storage-class tag and length."""
    if value is None:
        return _frame(b"N", b"")
    if isinstance(value, bool):
        # sqlite3 normally returns INTEGER, but bool is rejected as ambiguous
        # when supplied directly to this public encoder.
        raise TypeError("Python bool is not a distinct SQLite storage class")
    if isinstance(value, int):
        return _frame(b"I", str(value).encode("ascii"))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SQLiteExportError("non-finite REAL values cannot be checksummed")
        return _frame(b"R", struct.pack(">d", value))
    if isinstance(value, str):
        return _frame(b"T", value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _frame(b"B", bytes(value))
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def _quote_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
        raise SQLiteExportError("invalid SQLite identifier")
    return '"' + identifier.replace('"', '""') + '"'


def _table_primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    columns = [row[1] for row in rows]
    pk = sorted((row[5], row[1]) for row in rows if row[5])
    if not pk:
        raise SQLiteExportError(f"table has no declared primary key: {table}")
    if [ordinal for ordinal, _ in pk] != list(range(1, len(pk) + 1)):
        raise SQLiteExportError(f"primary-key ordinals are not contiguous: {table}")
    return [name for _, name in pk]


def _table_metadata(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    columns = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    if not columns:
        raise SQLiteExportError(f"table metadata unavailable: {table}")
    pk = _table_primary_key(connection, table)
    schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if schema_row is None or not schema_row[0]:
        raise SQLiteExportError(f"table SQL definition unavailable: {table}")
    return {
        "table": table,
        "columns": [
            {
                "name": row[1],
                "declared_type": row[2] or "",
                "not_null": bool(row[3]),
                "default": row[4],
                "primary_key_ordinal": int(row[5]),
            }
            for row in columns
        ],
        "primary_key": pk,
        "create_sql": schema_row[0],
    }


def _stream_table(
    connection: sqlite3.Connection, metadata: dict[str, Any], *, batch_size: int
) -> dict[str, Any]:
    table = metadata["table"]
    columns = [column["name"] for column in metadata["columns"]]
    quoted_columns = ", ".join(_quote_identifier(name) for name in columns)
    order_by = ", ".join(_quote_identifier(name) for name in metadata["primary_key"])
    query = (
        f"SELECT {quoted_columns} FROM {_quote_identifier(table)} "
        f"ORDER BY {order_by}"
    )
    digest = hashlib.sha256()
    digest.update(CHECKSUM_FORMAT.encode("ascii") + b"\x00")
    digest.update(canonical_json_bytes({
        "table": table,
        "columns": metadata["columns"],
        "primary_key": metadata["primary_key"],
    }))
    digest.update(b"\x00")
    cursor = connection.execute(query)
    count = 0
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            digest.update(b"ROW\x00")
            for value in row:
                digest.update(encode_sqlite_value(value))
            count += 1
    return {"row_count": count, "row_sha256": digest.hexdigest()}


def build_sqlite_export_manifest(
    database: str | Path,
    *,
    expected_schema_version: int = EXPECTED_SCHEMA_VERSION,
    batch_size: int = 512,
) -> dict[str, Any]:
    """Build a deterministic schema-v36 manifest without modifying the source."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 100_000:
        raise SQLiteExportError("batch_size must be an integer from 1 to 100000")
    if isinstance(expected_schema_version, bool) or not isinstance(expected_schema_version, int) or expected_schema_version < 1:
        raise SQLiteExportError("expected_schema_version must be a positive integer")
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise SQLiteExportError("source database does not exist")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise SQLiteExportError("could not open source database read-only") from exc

    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != expected_schema_version:
            raise SQLiteExportError(
                f"source schema version {version} does not match expected {expected_schema_version}"
            )
        migrations = [
            int(row[0]) for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        if migrations != list(range(1, version + 1)):
            raise SQLiteExportError("source migration history is not contiguous")

        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise SQLiteExportError("source SQLite integrity check failed")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise SQLiteExportError("source contains foreign-key violations")

        objects = connection.execute(
            """SELECT type, name, tbl_name, sql FROM sqlite_master
               WHERE type IN ('table','index','trigger','view')
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
        expected_counts = {"table": 47, "index": 25, "trigger": 139, "view": 0}
        if object_counts != expected_counts:
            raise SQLiteExportError(
                f"schema-v36 object inventory mismatch: {object_counts!r}"
            )

        table_names = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for table in table_names:
            metadata = _table_metadata(connection, table)
            checksums = _stream_table(connection, metadata, batch_size=batch_size)
            tables.append({
                **metadata,
                **checksums,
                "schema_sha256": hashlib.sha256(
                    canonical_json_bytes(metadata)
                ).hexdigest(),
            })

        # Verify the audit chain with the same canonical payload semantics as
        # the registry, including sequence continuity and the recorded head.
        from .canonical import sha256_fingerprint
        previous_hash = None
        previous_sequence = 0
        for row in connection.execute(
            """SELECT sequence,event_id,event_type,entity_type,entity_id,
                      occurred_at,policy_version,payload_json,previous_hash,event_hash
               FROM audit_events ORDER BY sequence"""
        ):
            if int(row["sequence"]) != previous_sequence + 1:
                raise SQLiteExportError("audit event sequence is not contiguous")
            try:
                payload = json.loads(row["payload_json"])
                expected_hash = sha256_fingerprint({
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "occurred_at": row["occurred_at"],
                    "policy_version": row["policy_version"],
                    "payload": payload,
                    "previous_hash": previous_hash,
                })
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise SQLiteExportError("audit event payload cannot be verified") from exc
            if row["previous_hash"] != previous_hash or row["event_hash"] != expected_hash:
                raise SQLiteExportError("audit chain verification failed")
            previous_hash = row["event_hash"]
            previous_sequence += 1

        head = connection.execute(
            "SELECT sequence,event_hash FROM audit_chain_head WHERE singleton_id=1"
        ).fetchone()
        if head is None or int(head[0]) != previous_sequence or head[1] != previous_hash:
            raise SQLiteExportError("audit chain head does not match verified event tip")

        manifest = {
            "format": MANIFEST_FORMAT,
            "checksum_format": CHECKSUM_FORMAT,
            "source": {
                "filename": path.name,
                "schema_version": version,
                "migration_versions": migrations,
                "integrity_check": integrity,
                "foreign_key_violations": 0,
                "object_counts": object_counts,
                "schema_sha256": hashlib.sha256(
                    canonical_json_bytes(object_inventory)
                ).hexdigest(),
                "audit_checkpoint": {
                    "sequence": previous_sequence,
                    "event_hash": previous_hash,
                },
            },
            "tables": tables,
        }
        manifest["manifest_sha256"] = hashlib.sha256(
            canonical_json_bytes(manifest)
        ).hexdigest()
        return manifest
    except sqlite3.Error as exc:
        raise SQLiteExportError("SQLite export manifest construction failed") from exc
    finally:
        connection.close()


def write_manifest_new(path: str | Path, manifest: dict[str, Any]) -> Path:
    """Atomically write a canonical manifest, refusing overwrite."""
    destination = Path(path).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"manifest destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError("manifest parent directory does not exist")
    rendered = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.link(temporary_name, destination)
        os.chmod(destination, 0o600)
        return destination
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only deterministic schema-v36 SQLite export manifest."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()
    manifest = build_sqlite_export_manifest(
        args.database, batch_size=args.batch_size
    )
    write_manifest_new(args.output, manifest)
    print(json.dumps({
        "status": "verified",
        "manifest_sha256": manifest["manifest_sha256"],
        "tables": len(manifest["tables"]),
        "output": str(args.output.resolve()),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
