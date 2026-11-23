from __future__ import annotations

import json

import pytest

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.postgres_audit import PostgresAuditError, append_audit_event


class FakeCursor:
    def __init__(self, head=(0, None), update_rowcount=1, anchor_exists=True):
        self.head = head
        self.update_rowcount = update_rowcount
        self.statements = []
        self.rowcount = None
        self._fetches = 0
        self.anchor_exists = anchor_exists

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        if sql.lstrip().startswith("UPDATE audit_chain_head"):
            self.rowcount = self.update_rowcount

    def fetchone(self):
        self._fetches += 1
        if self._fetches == 1:
            return self.head
        if self.anchor_exists and self.head and self.head[0] > 0:
            return (self.head[1],)
        return None


def _call(cursor):
    return append_audit_event(
        cursor,
        event_id="audit:test-1",
        event_type="record.created",
        entity_type="source",
        entity_id="source-1",
        occurred_at="2026-11-23T12:00:00Z",
        payload={"z": 2, "a": "বাংলা"},
        policy_version="policy/1",
    )


def test_append_locks_head_inserts_canonical_event_then_advances_head():
    cursor = FakeCursor()
    result = _call(cursor)

    assert "FOR UPDATE" in cursor.statements[0][0]
    assert cursor.statements[1][0].startswith("INSERT INTO audit_events")
    assert cursor.statements[2][0].startswith("UPDATE audit_chain_head")
    insert_params = cursor.statements[1][1]
    assert insert_params[0] == 1
    assert insert_params[7] == json.dumps(
        {"z": 2, "a": "বাংলা"}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    assert insert_params[8] is None
    assert result["sequence"] == 1
    assert result["previous_hash"] is None
    assert result["event_hash"] == sha256_fingerprint({
        "event_id": "audit:test-1",
        "event_type": "record.created",
        "entity_type": "source",
        "entity_id": "source-1",
        "occurred_at": "2026-11-23T12:00:00Z",
        "policy_version": "policy/1",
        "payload": {"a": "বাংলা", "z": 2},
        "previous_hash": None,
    })


def test_append_uses_existing_head_as_predecessor():
    cursor = FakeCursor(head=(7, "sha256:" + "a" * 64))
    result = _call(cursor)
    assert result["sequence"] == 8
    assert result["previous_hash"] == "sha256:" + "a" * 64
    assert cursor.statements[2][1][8] == "sha256:" + "a" * 64


def test_append_fails_closed_when_head_is_missing():
    with pytest.raises(PostgresAuditError, match="head is missing"):
        _call(FakeCursor(head=None))
    assert len(FakeCursor(head=None).statements) == 0


def test_append_fails_closed_when_head_compare_and_swap_loses():
    with pytest.raises(PostgresAuditError, match="compare-and-swap"):
        _call(FakeCursor(update_rowcount=0))


@pytest.mark.parametrize("payload", [None, [], "text"])
def test_append_rejects_non_object_payload(payload):
    cursor = FakeCursor()
    with pytest.raises(TypeError, match="JSON object"):
        append_audit_event(
            cursor, event_id="e", event_type="t", entity_type="x",
            entity_id="y", occurred_at="now", payload=payload,
        )
    assert cursor.statements == []


def test_append_rejects_non_finite_json_numbers():
    cursor = FakeCursor()
    with pytest.raises(ValueError):
        append_audit_event(
            cursor, event_id="e", event_type="t", entity_type="x",
            entity_id="y", occurred_at="now", payload={"bad": float("nan")},
        )
    assert cursor.statements == []

    
def test_append_refuses_head_that_does_not_match_durable_event():
    cursor = FakeCursor(
        head=(7, "sha256:" + "a" * 64),
        anchor_exists=False,
    )
    with pytest.raises(PostgresAuditError, match="durable event anchor"):
        _call(cursor)
    assert len(cursor.statements) == 2
