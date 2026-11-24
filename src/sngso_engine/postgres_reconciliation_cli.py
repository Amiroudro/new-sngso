"""Read-only schema-v36 SQLite-to-PostgreSQL dry-run command.

The command inspects source and target state and writes a local report. It never
imports rows, mutates either database, enables workers, or authorizes cutover.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from .canonical import canonical_json_bytes
from .postgres_reconciliation import plan_schema_v36_reconciliation
from .postgres_target_inspection import inspect_postgres_import_target
from .sqlite_export import build_sqlite_export_manifest, write_manifest_new


DRY_RUN_REPORT_FORMAT = "sngso-schema-v36-dry-run-report-v1"


def build_schema_v36_dry_run_report(
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
    mapping_spec: dict[str, Any] | None,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Build a redacted report; structural success remains INCOMPLETE."""
    plan = plan_schema_v36_reconciliation(
        source_manifest,
        target_inventory,
        mapping_spec,
    )
    target_identity = target_inventory.get("identity", {})
    report: dict[str, Any] = {
        "format": DRY_RUN_REPORT_FORMAT,
        "generated_at": generated_at,
        "outcome": "INCOMPLETE" if plan["structure_valid"] else "BLOCKED",
        "database_mutated": False,
        "import_performed": False,
        "import_authorized": False,
        "source": {
            "filename": source_manifest.get("source", {}).get("filename"),
            "schema_version": source_manifest.get("source", {}).get("schema_version"),
            "migration_versions": source_manifest.get("source", {}).get("migration_versions"),
            "integrity_check": source_manifest.get("source", {}).get("integrity_check"),
            "foreign_key_violations": source_manifest.get("source", {}).get("foreign_key_violations"),
            "object_counts": source_manifest.get("source", {}).get("object_counts"),
            "schema_sha256": source_manifest.get("source", {}).get("schema_sha256"),
            "audit_checkpoint": source_manifest.get("source", {}).get("audit_checkpoint"),
            "manifest_sha256": source_manifest.get("manifest_sha256"),
            "table_checksums": [
                {
                    "table": item.get("table"),
                    "row_count": item.get("row_count"),
                    "row_sha256": item.get("row_sha256"),
                    "schema_sha256": item.get("schema_sha256"),
                }
                for item in source_manifest.get("tables", [])
                if isinstance(item, dict)
            ],
        },
        "target": {
            "database_name": target_identity.get("database_name"),
            "schema_name": target_identity.get("schema_name"),
            "server_version": target_identity.get("server_version"),
            "migration_ledger_matches_packaged": target_inventory.get(
                "migration_ledger_matches_packaged"
            ),
            "table_count": len(target_inventory.get("tables", [])),
            "foreign_key_count": len(target_inventory.get("foreign_keys", [])),
            "inventory_sha256": target_inventory.get("inventory_sha256"),
        },
        "mapping_sha256": (
            mapping_spec.get("mapping_sha256")
            if isinstance(mapping_spec, dict)
            else None
        ),
        "plan": plan,
        "remaining_gates": list(plan.get("remaining_gates", [])),
    }
    report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    return report


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("mapping file is unreadable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("mapping file must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only schema-v36 SQLite/PostgreSQL reconciliation dry run. "
            "Never imports or authorizes cutover."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    dsn = os.environ.get("SNGSO_POSTGRES_DSN")
    if not dsn:
        print("SNGSO_POSTGRES_DSN is required; the value is never accepted as a command-line argument.", file=sys.stderr)
        return 2

    try:
        source_manifest = build_sqlite_export_manifest(args.source)
        mapping_spec = _load_mapping(args.mapping)
        try:
            import psycopg
        except ImportError:
            print("Install the optional postgres dependency to inspect the target.", file=sys.stderr)
            return 2

        connection = psycopg.connect(dsn)
        try:
            target_inventory = inspect_postgres_import_target(connection)
        finally:
            connection.close()

        report = build_schema_v36_dry_run_report(
            source_manifest,
            target_inventory,
            mapping_spec,
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        destination = write_manifest_new(args.output, report)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Dry run stopped safely: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "outcome": report["outcome"],
        "report_sha256": report["report_sha256"],
        "output": str(destination),
        "database_mutated": False,
        "import_performed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report["outcome"] == "INCOMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
