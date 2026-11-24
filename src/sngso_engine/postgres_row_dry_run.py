"""Read-only schema-v36 row reconciliation dry-run command.

The command combines existing snapshot-based evidence collectors. It never
imports data and never authorizes production cutover. Each collector owns its
own read-only snapshot; callers must quiesce target writes while collecting a
comparable report.
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
from .postgres_jsonb_semantic import prove_sqlite_jsonb_semantic_equivalence
from .postgres_reconciliation import plan_schema_v36_reconciliation
from .postgres_row_inspection import inspect_postgres_mapped_rows
from .postgres_target_inspection import inspect_postgres_import_target
from .sqlite_converted_row_inspection import (
    compare_converted_row_reports,
    inspect_sqlite_converted_rows,
)
from .sqlite_export import build_sqlite_export_manifest, write_manifest_new


REPORT_FORMAT = "sngso-schema-v36-row-dry-run-v1"


def build_row_dry_run_report(
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
    plan: dict[str, Any],
    source_rows: dict[str, Any] | None,
    target_rows: dict[str, Any] | None,
    row_comparison: dict[str, Any] | None,
    jsonb_proof: dict[str, Any] | None,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Create a redacted result that cannot claim import or cutover readiness."""
    structural = bool(plan.get("structure_valid"))
    row_evidence_present = all(
        item is not None for item in (source_rows, target_rows, row_comparison)
    )
    jsonb_applicable = any(
        column.get("conversion") == "json_text_to_jsonb"
        for table in (plan.get("tables", []) if structural else [])
        for column in table.get("columns", [])
    )
    jsonb_valid = (
        jsonb_proof is not None and jsonb_proof.get("outcome") == "VALID"
        if jsonb_applicable else True
    )
    row_match = (
        row_evidence_present
        and bool(row_comparison.get("non_json_values_match"))
        and bool(target_rows.get("all_row_counts_match_source"))
    )
    issues = list(plan.get("issues", []))
    if row_comparison:
        issues.extend(row_comparison.get("issues", []))
    if jsonb_proof and jsonb_proof.get("outcome") != "VALID":
        issues.append({"code": "JSONB_SEMANTIC_MISMATCH"})
    if structural and row_evidence_present and (not row_match or not jsonb_valid):
        outcome = "BLOCKED"
    elif structural:
        outcome = "INCOMPLETE"
    else:
        outcome = "BLOCKED"

    source = source_manifest.get("source", {})
    target_identity = target_inventory.get("identity", {})
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "generated_at": generated_at,
        "outcome": outcome,
        "database_mutated": False,
        "import_performed": False,
        "import_authorized": False,
        "row_data_inspected": row_evidence_present,
        "non_json_row_values_match": row_match if row_evidence_present else None,
        "jsonb_semantics": (
            jsonb_proof.get("outcome") if jsonb_proof else
            ("NOT_APPLICABLE" if not jsonb_applicable else "NOT_RUN")
        ),
        "target_audit_chain_compared": False,
        "cutover_ready": False,
        "target_evidence_snapshots": "SEPARATE_READ_ONLY_SNAPSHOTS",
        "source": {
            "schema_version": source.get("schema_version"),
            "schema_sha256": source.get("schema_sha256"),
            "manifest_sha256": source_manifest.get("manifest_sha256"),
            "table_count": len(source_manifest.get("tables", [])),
        },
        "target": {
            "database_name": target_identity.get("database_name"),
            "schema_name": target_identity.get("schema_name"),
            "server_version": target_identity.get("server_version"),
            "inventory_sha256": target_inventory.get("inventory_sha256"),
        },
        "mapping_sha256": plan.get("mapping_sha256"),
        "plan": plan,
        "row_comparison": row_comparison,
        "jsonb_proof": (
            {
                "outcome": jsonb_proof.get("outcome"),
                "proof_sha256": jsonb_proof.get("proof_sha256"),
                "table_count": len(jsonb_proof.get("table_reports", [])),
                "read_only": jsonb_proof.get("read_only"),
            }
            if jsonb_proof else None
        ),
        "issues": issues,
        "remaining_gates": [
            "AUDIT_CHAIN_IMPORT_AND_INDEPENDENT_VERIFICATION",
            "ISOLATED_BULK_IMPORT_AND_CONSTRAINT_RESTORATION",
            "RESTRICTED_IMPORT_ROLE_PROOF",
            "RESTORE_AND_ROLLBACK_REHEARSAL",
            "PRODUCTION_AND_HIGH_AVAILABILITY_ACCEPTANCE",
        ],
    }
    report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    return report


def _load_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("mapping file must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only schema-v36 row reconciliation dry run. "
            "Requires a quiesced disposable target; never imports or cuts over."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args(argv)

    dsn = os.environ.get("SNGSO_POSTGRES_DSN")
    if not dsn:
        print("Set SNGSO_POSTGRES_DSN; credentials are not accepted as command-line arguments.", file=sys.stderr)
        return 2

    try:
        source_manifest = build_sqlite_export_manifest(args.source)
        mapping = _load_mapping(args.mapping)
        source_rows = inspect_sqlite_converted_rows(
            args.source, source_manifest, mapping, batch_size=args.batch_size
        )
        try:
            import psycopg
        except ImportError:
            print("Install the optional postgres dependency to inspect the target.", file=sys.stderr)
            return 2

        try:
            connection = psycopg.connect(dsn)
            try:
                target_inventory = inspect_postgres_import_target(connection)
                plan = plan_schema_v36_reconciliation(
                    source_manifest, target_inventory, mapping
                )
                target_rows = None
                row_comparison = None
                jsonb_proof = None
                if plan.get("structure_valid"):
                    target_rows = inspect_postgres_mapped_rows(
                        connection, source_manifest, target_inventory, mapping,
                        batch_size=args.batch_size,
                    )
                    row_comparison = compare_converted_row_reports(
                        source_rows, target_rows
                    )
                    has_jsonb = any(
                        column.get("conversion") == "json_text_to_jsonb"
                        for table in mapping.get("tables", [])
                        for column in table.get("columns", [])
                    )
                    if has_jsonb:
                        jsonb_proof = prove_sqlite_jsonb_semantic_equivalence(
                            args.source, connection, source_manifest,
                            target_inventory, mapping, batch_size=args.batch_size,
                        )
            finally:
                connection.close()
        except psycopg.Error:
            print(
                "PostgreSQL reconciliation failed; verify read-only target connectivity.",
                file=sys.stderr,
            )
            return 2

        report = build_row_dry_run_report(
            source_manifest, target_inventory, plan,
            source_rows, target_rows, row_comparison, jsonb_proof,
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
        "cutover_ready": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report["outcome"] == "INCOMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
