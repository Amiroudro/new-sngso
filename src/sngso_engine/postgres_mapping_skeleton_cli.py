"""Read-only CLI that writes a draft schema-v36 mapping skeleton."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .postgres_mapping_skeleton import build_schema_v36_mapping_skeleton
from .postgres_target_inspection import (
    PostgresInspectionError,
    inspect_postgres_import_target,
)
from .sqlite_export import build_sqlite_export_manifest, write_manifest_new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write unapproved exact-name suggestions for a schema-v36 mapping. "
            "This command never imports or mutates a database."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-target-database", required=True)
    parser.add_argument("--expected-target-schema", required=True)
    args = parser.parse_args(argv)

    dsn = os.environ.get("SNGSO_POSTGRES_DSN")
    if not dsn:
        print(
            "SNGSO_POSTGRES_DSN is required; its value is never accepted on the command line.",
            file=sys.stderr,
        )
        return 2

    try:
        source_manifest = build_sqlite_export_manifest(args.source)
        try:
            import psycopg
        except ImportError:
            print("Install the optional postgres dependency to inspect the target.", file=sys.stderr)
            return 2

        try:
            connection = psycopg.connect(dsn)
            try:
                target_inventory = inspect_postgres_import_target(connection)
            finally:
                connection.close()
        except (psycopg.Error, PostgresInspectionError):
            print("PostgreSQL target inspection failed; connection details were not included.", file=sys.stderr)
            return 2

        identity = target_inventory.get("identity", {})
        if identity.get("database_name") != args.expected_target_database:
            print("PostgreSQL target database identity does not match the expected value.", file=sys.stderr)
            return 2
        if identity.get("schema_name") != args.expected_target_schema:
            print("PostgreSQL target schema identity does not match the expected value.", file=sys.stderr)
            return 2

        skeleton = build_schema_v36_mapping_skeleton(source_manifest, target_inventory)
        write_manifest_new(args.output, skeleton)
    except (OSError, ValueError) as exc:
        print(f"Mapping skeleton was not written: {exc}", file=sys.stderr)
        return 2

    print(
        "Draft mapping skeleton written. It is unapproved, may contain unresolved gaps, "
        "and cannot authorize import."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
