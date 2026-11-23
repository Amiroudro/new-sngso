"""PostgreSQL append primitive for the registry's globally ordered audit chain.

The caller must invoke this inside the same run_atomic() transaction as the
domain mutation. The singleton head row is the serialization point: it remains
locked until the surrounding transaction commits or rolls back.
"""
from __future__ import annotations

import json
from typing import Any

from .canonical import sha256_fingerprint


class PostgresAuditError(RuntimeError):
    """Raised when the durable PostgreSQL audit-chain head is invalid."""


def append_audit_event(
    cursor: Any,
    *,
    event_id: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    occurred_at: str,
    payload: dict[str, Any],
    policy_version: str | None = None,
) -> dict[str, Any]:
    """Append one event while holding the singleton audit head row lock.

    Required schema contract:
      audit_chain_head(singleton_id=1, sequence, event_hash)
      audit_events(sequence, event_id, event_type, entity_type, entity_id,
                   occurred_at, policy_version, payload_json,
                   previous_hash, event_hash)

    The head row must be provisioned by a reviewed PostgreSQL migration with
    sequence=0 and event_hash=NULL. This function deliberately refuses to
    bootstrap or repair it: doing so during an ordinary write could conceal
    truncation or an incomplete migration.
    """
    required = {
        "event_id": event_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "occurred_at": occurred_at,
    }
    if any(not isinstance(value, str) or not value.strip() for value in required.values()):
        raise ValueError("audit event identity, type, and timestamp fields are required")
    if not isinstance(payload, dict):
        raise TypeError("audit payload must be a JSON object")

    payload_json = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )

    cursor.execute(
        """SELECT sequence, event_hash
           FROM audit_chain_head
           WHERE singleton_id = 1
           FOR UPDATE"""
    )
    head = cursor.fetchone()
    if head is None:
        raise PostgresAuditError("audit chain head is missing; refusing an unanchored append")
    previous_sequence, previous_hash = head
    if not isinstance(previous_sequence, int) or previous_sequence < 0:
        raise PostgresAuditError("audit chain head sequence is invalid")
    if previous_sequence == 0 and previous_hash is not None:
        raise PostgresAuditError("empty audit chain head must have a NULL hash")
    if previous_sequence > 0 and (
        not isinstance(previous_hash, str) or not previous_hash.startswith("sha256:")
    ):
        raise PostgresAuditError("non-empty audit chain head has an invalid hash")
    if previous_sequence > 0:
        cursor.execute(
            """SELECT event_hash FROM audit_events WHERE sequence = %s""",
            (previous_sequence,),
        )
        anchor = cursor.fetchone()
        if anchor is None or anchor[0] != previous_hash:
            raise PostgresAuditError(
                "audit chain head does not match its durable event anchor"
            )

    event_hash = sha256_fingerprint({
        "event_id": event_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "occurred_at": occurred_at,
        "policy_version": policy_version,
        "payload": json.loads(payload_json),
        "previous_hash": previous_hash,
    })
    sequence = previous_sequence + 1
    cursor.execute(
        """INSERT INTO audit_events
           (sequence, event_id, event_type, entity_type, entity_id, occurred_at,
            policy_version, payload_json, previous_hash, event_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            sequence, event_id, event_type, entity_type, entity_id, occurred_at,
            policy_version, payload_json, previous_hash, event_hash,
        ),
    )
    cursor.execute(
        """UPDATE audit_chain_head
           SET sequence = %s, event_hash = %s
           WHERE singleton_id = 1 AND sequence = %s
             AND event_hash IS NOT DISTINCT FROM %s""",
        (sequence, event_hash, previous_sequence, previous_hash),
    )
    if getattr(cursor, "rowcount", None) != 1:
        raise PostgresAuditError("audit chain head compare-and-swap failed")
    return {"sequence": sequence, "event_id": event_id, "event_hash": event_hash,
            "previous_hash": previous_hash}
