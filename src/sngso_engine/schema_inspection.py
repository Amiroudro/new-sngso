"""Read-only inspection of a live SQLite registry schema for migration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class SchemaInspectionError(RuntimeError):
    """Raised when a registry cannot be safely inspected."""


def inspect_sqlite_schema(database: str | Path) -> dict[str, Any]:
    """Return a deterministic, read-only schema and integrity inventory.

    The target must already exist. The connection is opened with SQLite URI
    mode=ro, so inspection cannot create a missing database or mutate it.
    SQL definitions are included verbatim to support review of CHECK clauses,
    partial indexes, foreign keys, and trigger behavior.
    """
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
        foreign_key_violations = [
            {
                "table": str(row[0]),
                "rowid": row[1],
                "parent": str(row[2]),
                "fkid": int(row[3]),
            }
            for row in connection.execute("PRAGMA foreign_key_check")
        ]
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
            "integrity_check": integrity_rows,
            "foreign_key_violations": foreign_key_violations,
            "object_counts": counts,
            "schema_sha256": hashlib.sha256(schema_json).hexdigest(),
            "objects": objects,
            "healthy": (
                version > 0
                and integrity_rows == ["ok"]
                and not foreign_key_violations
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
