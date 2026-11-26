"""Read-only import preflight for a namespaced disposable PostgreSQL target."""
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
from .postgres_import_preflight import inspect_empty_import_target
from .postgres_reconciliation import plan_schema_v36_reconciliation
from .postgres_target_inspection import inspect_postgres_import_target
from .sqlite_export import build_sqlite_export_manifest, write_manifest_new


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input file must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only preflight for a reviewed schema-v36 mapping and empty "
            "sngso_import_* PostgreSQL schema. Does not import or authorize cutover."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--expected-target-database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    dsn = os.environ.get("SNGSO_IMPORT_POSTGRES_DSN")
    if not dsn:
        print("Set SNGSO_IMPORT_POSTGRES_DSN; credentials are not CLI arguments.", file=sys.stderr)
        return 2

    try:
        manifest = build_sqlite_export_manifest(args.source)
        mapping = _load_object(args.mapping)
        try:
            import psycopg
        except ImportError:
            print("Install the optional postgres dependency to inspect the target.", file=sys.stderr)
            return 2

        try:
            connection = psycopg.connect(dsn)
            try:
                inventory = inspect_postgres_import_target(connection)
                plan = plan_schema_v36_reconciliation(manifest, inventory, mapping)
                emptiness = inspect_empty_import_target(connection, inventory)
            finally:
                connection.close()
        except psycopg.Error:
            print("PostgreSQL preflight failed; verify disposable-target connectivity.", file=sys.stderr)
            return 2

        identity = inventory.get("identity", {})
        issues = list(plan.get("issues", []))
        issues.extend(emptiness.get("issue_codes", []))
        if identity.get("database_name") != args.expected_target_database:
            issues.append({"code": "TARGET_DATABASE_IDENTITY_MISMATCH"})
        schema_name = identity.get("schema_name")
        if not isinstance(schema_name, str) or not schema_name.startswith("sngso_import_"):
            issues.append({"code": "TARGET_SCHEMA_NOT_ISOLATED_IMPORT_NAMESPACE"})
        passed = (
            plan.get("structure_valid") is True
            and emptiness.get("target_empty") is True
            and identity.get("database_name") == args.expected_target_database
            and isinstance(schema_name, str)
            and schema_name.startswith("sngso_import_")
            and not issues
        )
        report = {
            "format": "sngso-schema-v36-import-preflight-v1",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "outcome": "PREFLIGHT_PASSED" if passed else "BLOCKED",
            "database_mutated": False,
            "import_performed": False,
            "import_authorized": False,
            "cutover_ready": False,
            "importer_implemented": False,
            "source_manifest_sha256": manifest["manifest_sha256"],
            "mapping_sha256": mapping.get("mapping_sha256"),
            "target_inventory_sha256": inventory.get("inventory_sha256"),
            "target_database": identity.get("database_name"),
            "target_schema": schema_name,
            "target_empty": emptiness.get("target_empty"),
            "target_table_count": len(inventory.get("tables", [])),
            "plan": plan,
            "issues": issues,
            "next_gates": [
                "ONE_TRANSACTION_IMPORT_WITH_ACTIVE_CONSTRAINTS",
                "SEQUENCE_STATE_RECONCILIATION",
                "POST_COMMIT_INDEPENDENT_ROW_AND_AUDIT_VERIFICATION",
                "ROLLBACK_AND_RESTORE_REHEARSAL",
            ],
        }
        report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
        destination = write_manifest_new(args.output, report)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Import preflight stopped safely: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "outcome": report["outcome"],
        "report_sha256": report["report_sha256"],
        "output": str(destination),
        "database_mutated": False,
        "import_performed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
