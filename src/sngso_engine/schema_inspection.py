"""Read-only inspection of a live SQLite registry schema for migration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .canonical import sha256_fingerprint


class SchemaInspectionError(RuntimeError):
    """Raised when a registry cannot be safely inspected."""


def inspect_sqlite_schema(
    database: str | Path,
    *,
    expected_audit_checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, read-only schema and integrity inventory.

    The target must already exist. The connection is opened with SQLite URI
    mode=ro, so inspection cannot create a missing database or mutate it.
    SQL definitions are included verbatim to support review of CHECK clauses,
    partial indexes, foreign keys, and trigger behavior.
    """
    if expected_audit_checkpoint is not None:
        if set(expected_audit_checkpoint) != {"sequence", "event_hash"}:
            raise SchemaInspectionError(
                "expected audit checkpoint must contain sequence and event_hash"
            )
        sequence = expected_audit_checkpoint["sequence"]
        event_hash = expected_audit_checkpoint["event_hash"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or (event_hash is not None and not isinstance(event_hash, str))
            or (sequence == 0 and event_hash is not None)
            or (sequence > 0 and not event_hash)
        ):
            raise SchemaInspectionError("expected audit checkpoint is malformed")

    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise SchemaInspectionError(f"database file does not exist: {path}")

    uri = path.as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise SchemaInspectionError("could not open database read-only") from exc

    try:
        connection.row_factory = sqlite3.Row
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        integrity_rows = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        migration_rows = [
            {"version": int(row["version"]), "applied_at": str(row["applied_at"])}
            for row in connection.execute(
                "SELECT version, applied_at FROM schema_migrations ORDER BY version"
            )
        ]
        migration_history_valid = [
            row["version"] for row in migration_rows
        ] == list(range(1, version + 1))
        foreign_key_violations = [
            {
                "table": str(row[0]),
                "rowid": row[1],
                "parent": str(row[2]),
                "fkid": int(row[3]),
            }
            for row in connection.execute("PRAGMA foreign_key_check")
        ]
        audit_chain_valid = True
        previous_hash = None
        for row in connection.execute(
            """
            SELECT event_id, event_type, entity_type, entity_id, occurred_at,
                   policy_version, payload_json, previous_hash, event_hash
            FROM audit_events ORDER BY sequence
            """
        ):
            try:
                payload = json.loads(row["payload_json"])
                expected = sha256_fingerprint(
                    {
                        "event_id": row["event_id"],
                        "event_type": row["event_type"],
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "occurred_at": row["occurred_at"],
                        "policy_version": row["policy_version"],
                        "payload": payload,
                        "previous_hash": previous_hash,
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                audit_chain_valid = False
                break
            if row["previous_hash"] != previous_hash or row["event_hash"] != expected:
                audit_chain_valid = False
                break
            previous_hash = row["event_hash"]

        tip = connection.execute(
            "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        actual_audit_checkpoint = (
            {"sequence": 0, "event_hash": None}
            if tip is None
            else {"sequence": int(tip["sequence"]), "event_hash": str(tip["event_hash"])}
        )
        external_audit_checkpoint_matches = (
            None
            if expected_audit_checkpoint is None
            else actual_audit_checkpoint == expected_audit_checkpoint
        )

        objects = [
            {
                "type": str(row["type"]),
                "name": str(row["name"]),
                "table_name": str(row["tbl_name"]),
                "sql": row["sql"],
            }
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger', 'view')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ]
        schema_json = json.dumps(
            objects, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        counts = {
            kind: sum(item["type"] == kind for item in objects)
            for kind in ("table", "index", "trigger", "view")
        }
        return {
            "format": "sngso-sqlite-schema-inventory-v1",
            "database": path.name,
            "schema_version": version,
            "migration_history": migration_rows,
            "migration_history_valid": migration_history_valid,
            "integrity_check": integrity_rows,
            "foreign_key_violations": foreign_key_violations,
            "audit_chain_valid": audit_chain_valid,
            "audit_checkpoint": actual_audit_checkpoint,
            "external_audit_checkpoint_matches": external_audit_checkpoint_matches,
            "object_counts": counts,
            "schema_sha256": hashlib.sha256(schema_json).hexdigest(),
            "objects": objects,
            "healthy": (
                version > 0
                and integrity_rows == ["ok"]
                and migration_history_valid
                and not foreign_key_violations
                and audit_chain_valid
                and external_audit_checkpoint_matches is not False
            ),
        }
    except sqlite3.Error as exc:
        raise SchemaInspectionError("SQLite schema inspection failed") from exc
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a SQLite registry without modifying it."
    )
    parser.add_argument("database", type=Path, help="existing SQLite database file")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path; stdout is used when omitted",
    )
    args = parser.parse_args()
    report = inspect_sqlite_schema(args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
