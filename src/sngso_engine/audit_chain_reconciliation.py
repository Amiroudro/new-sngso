"""Read-only source/target audit-chain verification for schema-v36 migration evidence."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes, sha256_fingerprint
from .sqlite_export import EXPECTED_SCHEMA_VERSION, MANIFEST_FORMAT


_FIELDS = (
    "event_id", "event_type", "entity_type", "entity_id", "occurred_at",
    "policy_version",
)
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class AuditChainReconciliationError(RuntimeError):
    """Raised when audit evidence cannot be inspected safely."""


def _manifest_digest(manifest: dict[str, Any]) -> str:
    body = dict(manifest)
    supplied = body.pop("manifest_sha256", None)
    actual = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if supplied != actual:
        raise AuditChainReconciliationError("source manifest fingerprint mismatch")
    return actual


def _checkpoint(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"sequence", "event_hash"}:
        raise AuditChainReconciliationError(f"{label} checkpoint is malformed")
    sequence = value.get("sequence")
    event_hash = value.get("event_hash")
    if (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
        or (sequence == 0 and event_hash is not None)
        or (sequence > 0 and (
            not isinstance(event_hash, str) or _HASH.fullmatch(event_hash) is None
        ))
    ):
        raise AuditChainReconciliationError(f"{label} checkpoint is malformed")
    return {"sequence": sequence, "event_hash": event_hash}


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise AuditChainReconciliationError("target schema identifier is invalid")
    return '"' + value.replace('"', '""') + '"'


def _event_hash(row: dict[str, Any], payload: Any, previous_hash: str | None) -> str:
    return sha256_fingerprint({
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "occurred_at": row["occurred_at"],
        "policy_version": row["policy_version"],
        "payload": payload,
        "previous_hash": previous_hash,
    })


def compare_sqlite_postgres_audit_chains(
    database: str | Path,
    connection: Any,
    source_manifest: dict[str, Any],
    target_inventory: dict[str, Any],
    *,
    expected_external_checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify both complete chains and compare events using read-only snapshots.

    The SQLite chain is read in one mode=ro transaction; the PostgreSQL chain
    and singleton head are read in one REPEATABLE READ READ ONLY transaction.
    An external checkpoint is optional, but without one the report cannot detect
    a source tail truncated before the manifest snapshot was created.
    """
    if not isinstance(source_manifest, dict) or source_manifest.get("format") != MANIFEST_FORMAT:
        raise AuditChainReconciliationError("unsupported SQLite source manifest")
    _manifest_digest(source_manifest)
    if source_manifest.get("source", {}).get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise AuditChainReconciliationError("source manifest is not schema version 36")
    manifest_checkpoint = _checkpoint(
        source_manifest.get("source", {}).get("audit_checkpoint"),
        "manifest",
    )
    external_checkpoint = (
        _checkpoint(expected_external_checkpoint, "external")
        if expected_external_checkpoint is not None else None
    )

    status = getattr(getattr(getattr(connection, "info", None), "transaction_status", None), "name", None)
    if status != "IDLE":
        raise AuditChainReconciliationError("PostgreSQL connection must be idle")
    if not callable(getattr(connection, "transaction", None)) or not callable(
        getattr(connection, "cursor", None)
    ):
        raise TypeError("connection must provide psycopg 3 transaction() and cursor()")

    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise AuditChainReconciliationError("SQLite source database does not exist")
    try:
        sqlite = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise AuditChainReconciliationError("could not open SQLite source read-only") from exc

    issue_codes: set[str] = set()
    source_chain_valid = True
    target_chain_valid = True
    source_count = 0
    target_count = 0
    matching_count = 0
    source_previous: str | None = None
    target_previous: str | None = None
    source_tip: dict[str, Any] = {"sequence": 0, "event_hash": None}
    target_tip: dict[str, Any] = {"sequence": 0, "event_hash": None}
    target_head: dict[str, Any] | None = None

    try:
        sqlite.row_factory = sqlite3.Row
        sqlite.execute("PRAGMA query_only = ON")
        sqlite.execute("BEGIN")
        version = int(sqlite.execute("PRAGMA user_version").fetchone()[0])
        if version != EXPECTED_SCHEMA_VERSION:
            raise AuditChainReconciliationError("SQLite source schema version changed")
        source_cursor = sqlite.execute(
            """SELECT sequence, event_id, event_type, entity_type, entity_id,
                      occurred_at, policy_version, payload_json, previous_hash, event_hash
               FROM audit_events ORDER BY sequence"""
        )

        schema = target_inventory.get("identity", {}).get("schema_name")
        quoted_schema = _quote_identifier(schema)
        events_table = f"{quoted_schema}.\"audit_events\""
        head_table = f"{quoted_schema}.\"audit_chain_head\""

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute(
                    f"SELECT singleton_id, sequence, event_hash FROM {head_table} ORDER BY singleton_id"
                )
                heads = cursor.fetchall()
                if len(heads) != 1 or heads[0][0] != 1:
                    issue_codes.add("TARGET_AUDIT_HEAD_MISSING_OR_DUPLICATED")
                else:
                    try:
                        target_head = _checkpoint(
                            {"sequence": heads[0][1], "event_hash": heads[0][2]},
                            "target",
                        )
                    except AuditChainReconciliationError:
                        issue_codes.add("TARGET_AUDIT_HEAD_MALFORMED")

            with connection.cursor(name="sngso_audit_chain_read") as target_cursor:
                target_cursor.execute(
                    f"""SELECT sequence, event_id, event_type, entity_type, entity_id,
                               occurred_at, policy_version, payload_json,
                               previous_hash, event_hash
                        FROM {events_table} ORDER BY sequence"""
                )
                target_iter = iter(lambda: target_cursor.fetchmany(512), [])
                target_rows = (row for batch in target_iter for row in batch)
                target_row = next(target_rows, None)

                for source in source_cursor:
                    source_count += 1
                    expected_sequence = source_count
                    source_row = dict(source)
                    if source_row["sequence"] != expected_sequence:
                        issue_codes.add("SOURCE_AUDIT_SEQUENCE_GAP")
                        source_chain_valid = False
                    try:
                        source_payload = json.loads(source_row["payload_json"])
                        source_hash = _event_hash(source_row, source_payload, source_previous)
                        if source_row["previous_hash"] != source_previous or source_row["event_hash"] != source_hash:
                            issue_codes.add("SOURCE_AUDIT_CHAIN_INVALID")
                            source_chain_valid = False
                    except (TypeError, ValueError, json.JSONDecodeError):
                        source_payload = None
                        issue_codes.add("SOURCE_AUDIT_PAYLOAD_INVALID")
                        source_chain_valid = False
                    source_previous = source_row.get("event_hash")
                    source_tip = {
                        "sequence": source_row["sequence"],
                        "event_hash": source_row["event_hash"],
                    }

                    if target_row is None:
                        issue_codes.add("TARGET_AUDIT_EVENT_MISSING")
                        continue

                    target_count += 1
                    target_data = dict(zip(
                        ("sequence", *_FIELDS, "payload_json", "previous_hash", "event_hash"),
                        target_row,
                    ))
                    if target_data["sequence"] != target_count:
                        issue_codes.add("TARGET_AUDIT_SEQUENCE_GAP")
                        target_chain_valid = False
                    try:
                        target_payload = json.loads(target_data["payload_json"])
                        target_hash = _event_hash(target_data, target_payload, target_previous)
                        if target_data["previous_hash"] != target_previous or target_data["event_hash"] != target_hash:
                            issue_codes.add("TARGET_AUDIT_CHAIN_INVALID")
                            target_chain_valid = False
                    except (TypeError, ValueError, json.JSONDecodeError):
                        target_payload = None
                        issue_codes.add("TARGET_AUDIT_PAYLOAD_INVALID")
                        target_chain_valid = False
                    target_previous = target_data.get("event_hash")
                    target_tip = {
                        "sequence": target_data["sequence"],
                        "event_hash": target_data["event_hash"],
                    }

                    same = (
                        source_row["sequence"] == target_data["sequence"]
                        and all(source_row[field] == target_data[field] for field in _FIELDS)
                        and canonical_json_bytes(source_payload) == canonical_json_bytes(target_payload)
                        and source_row["previous_hash"] == target_data["previous_hash"]
                        and source_row["event_hash"] == target_data["event_hash"]
                    )
                    if same:
                        matching_count += 1
                    else:
                        issue_codes.add("AUDIT_EVENT_MISMATCH")
                    target_row = next(target_rows, None)

                while target_row is not None:
                    target_count += 1
                    issue_codes.add("TARGET_AUDIT_EVENT_EXTRA")
                    target_row = next(target_rows, None)

    except sqlite3.Error as exc:
        raise AuditChainReconciliationError("SQLite audit-chain inspection failed") from exc
    finally:
        sqlite.close()

    if source_tip != manifest_checkpoint:
        issue_codes.add("SOURCE_AUDIT_TIP_DIFFERS_FROM_MANIFEST")
    if target_head is None or target_head != target_tip:
        issue_codes.add("TARGET_AUDIT_HEAD_DIFFERS_FROM_EVENTS")
    if target_tip != source_tip:
        issue_codes.add("SOURCE_TARGET_AUDIT_TIP_MISMATCH")
    if external_checkpoint is not None and source_tip != external_checkpoint:
        issue_codes.add("SOURCE_AUDIT_TIP_DIFFERS_FROM_EXTERNAL_CHECKPOINT")

    events_match = source_count == target_count == matching_count
    target_head_matches = target_head is not None and target_head == target_tip
    source_anchor_matches = external_checkpoint is None or source_tip == external_checkpoint
    if issue_codes:
        outcome = "BLOCKED"
    elif external_checkpoint is None:
        outcome = "INCOMPLETE"
    else:
        outcome = "VALID"

    report: dict[str, Any] = {
        "format": "sngso-audit-chain-reconciliation-v1",
        "outcome": outcome,
        "read_only": True,
        "source_chain_valid": source_chain_valid,
        "target_chain_valid": target_chain_valid,
        "events_match": events_match,
        "target_head_matches_events": target_head_matches,
        "independent_source_checkpoint_supplied": external_checkpoint is not None,
        "source_external_checkpoint_matches": source_anchor_matches if external_checkpoint else None,
        "source_event_count": source_count,
        "target_event_count": target_count,
        "matching_event_count": matching_count,
        "source_tip": source_tip,
        "target_tip": target_tip,
        "target_head": target_head,
        "issue_codes": sorted(issue_codes),
        "remaining_gates": (
            [] if outcome == "VALID" else ["INDEPENDENT_SOURCE_AUDIT_CHECKPOINT"]
            if not external_checkpoint else ["RESOLVE_AUDIT_CHAIN_DISCREPANCIES"]
        ),
    }
    report["report_sha256"] = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    return report
