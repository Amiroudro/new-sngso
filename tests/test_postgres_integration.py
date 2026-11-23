from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.postgres_audit import append_audit_event
from sngso_engine.postgres_migration_runner import apply_postgres_migrations
from sngso_engine.postgres_transaction import run_atomic


@pytest.fixture
def postgres_schema():
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is not configured")

    schema = "sngso_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    try:
        with connect() as connection:
            assert apply_postgres_migrations(connection) == (1, 2)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def _append(connect, index):
    with connect() as connection:
        return run_atomic(
            connection,
            lambda cursor: append_audit_event(
                cursor,
                event_id=f"parallel-{index}",
                event_type="integration.test",
                entity_type="test",
                entity_id=str(index),
                occurred_at="2026-11-23T00:00:00Z",
                payload={"index": index},
            ),
        )


def test_postgres_migrations_execute_and_are_idempotent(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        assert apply_postgres_migrations(connection) == ()
        rows = connection.execute(
            "SELECT version, name FROM postgres_schema_migrations ORDER BY version"
        ).fetchall()
        assert rows == [(1, "0001_audit_chain.sql")]


def test_postgres_audit_append_serializes_independent_connections(postgres_schema):
    connect = postgres_schema
    count = 16
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: _append(connect, index), range(count)))

    assert sorted(result["sequence"] for result in results) == list(range(1, count + 1))
    with connect() as connection:
        rows = connection.execute(
            "SELECT sequence, previous_hash, event_hash FROM audit_events ORDER BY sequence"
        ).fetchall()
        head = connection.execute(
            "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()

    assert len(rows) == count
    assert rows[0][1] is None
    for previous, current in zip(rows, rows[1:]):
        assert current[1] == previous[2]
    assert head == (count, rows[-1][2])

    with connect() as connection:
        events = connection.execute(
            "SELECT sequence, event_id, event_type, entity_type, entity_id, occurred_at, "
            "policy_version, payload_json, previous_hash, event_hash "
            "FROM audit_events ORDER BY sequence"
        ).fetchall()
    for event in events:
        (sequence, event_id, event_type, entity_type, entity_id, occurred_at,
         policy_version, payload_json, previous_hash, event_hash) = event
        expected = sha256_fingerprint({
            "event_id": event_id, "event_type": event_type,
            "entity_type": entity_type, "entity_id": entity_id,
            "occurred_at": occurred_at, "policy_version": policy_version,
            "payload": json.loads(payload_json), "previous_hash": previous_hash,
        })
        assert event_hash == expected, f"audit hash mismatch at sequence {sequence}"


def test_postgres_audit_event_and_head_roll_back_together(postgres_schema):
    connect = postgres_schema

    def fail_after_append(cursor):
        append_audit_event(
            cursor,
            event_id="must-roll-back",
            event_type="integration.test",
            entity_type="test",
            entity_id="rollback",
            occurred_at="2026-11-23T00:00:00Z",
            payload={"rollback": True},
        )
        raise RuntimeError("injected failure after audit insert and head update")

    with connect() as connection:
        with pytest.raises(RuntimeError, match="injected failure"):
            run_atomic(connection, fail_after_append)

    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
        assert connection.execute(
            "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone() == (0, None)



def test_postgres_default_runtime_role_cannot_mutate_audit_storage(postgres_schema):
    connect = postgres_schema
    role_name = "sngso_audit_reader_" + uuid.uuid4().hex[:12]
    schema_name = None
    try:
        with connect() as owner:
            schema_name = owner.execute("SELECT current_schema()").fetchone()[0]
            owner.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name))
            )
            owner.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            owner.execute(
                sql.SQL("GRANT SELECT ON audit_events TO {}").format(
                    sql.Identifier(role_name)
                )
            )

        with connect() as restricted:
            restricted.autocommit = True
            restricted.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(role_name))
            )
            assert restricted.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
            denied_statements = (
                "UPDATE audit_events SET event_type = 'tampered' WHERE false",
                "DELETE FROM audit_events WHERE false",
                "TRUNCATE audit_events",
                "UPDATE audit_chain_head SET sequence = 0 WHERE singleton_id = 1",
                "DELETE FROM audit_chain_head WHERE singleton_id = 1",
                "TRUNCATE audit_chain_head",
            )
            for statement in denied_statements:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    restricted.execute(statement)
    finally:
        if schema_name is not None:
            with psycopg.connect(
                os.environ["TEST_POSTGRES_DSN"], autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )



def test_postgres_runtime_writer_can_append_but_cannot_rewrite_audit_head(postgres_schema):
    connect = postgres_schema
    role_name = "sngso_audit_writer_" + uuid.uuid4().hex[:12]
    schema_name = None
    try:
        with connect() as owner:
            schema_name = owner.execute("SELECT current_schema()").fetchone()[0]
            owner.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name))
            )
            owner.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(role_name)
                )
            )
            owner.execute(
                sql.SQL("GRANT SELECT, INSERT ON audit_events TO {}").format(
                    sql.Identifier(role_name)
                )
            )
            owner.execute(
                sql.SQL("GRANT SELECT, UPDATE ON audit_chain_head TO {}").format(
                    sql.Identifier(role_name)
                )
            )

        with connect() as writer:
            writer.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_name)))
            appended = run_atomic(
                writer,
                lambda cursor: append_audit_event(
                    cursor,
                    event_id="restricted-writer-append",
                    event_type="integration.test",
                    entity_type="test",
                    entity_id="restricted-writer",
                    occurred_at="2026-11-23T00:00:00Z",
                    payload={"writer": "restricted"},
                ),
            )
            assert appended["sequence"] == 1

        with connect() as restricted:
            restricted.autocommit = True
            restricted.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(role_name))
            )
            with pytest.raises(psycopg.errors.CheckViolation, match="advance exactly one"):
                restricted.execute(
                    "UPDATE audit_chain_head SET sequence = 0, event_hash = NULL "
                    "WHERE singleton_id = 1"
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute(
                    "DELETE FROM audit_chain_head WHERE singleton_id = 1"
                )

        with connect() as owner:
            assert owner.execute(
                "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
            ).fetchone() == (1, appended["event_hash"])
    finally:
        if schema_name is not None:
            with psycopg.connect(
                os.environ["TEST_POSTGRES_DSN"], autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )


def test_postgres_duplicate_audit_event_id_rolls_back_without_advancing_chain(postgres_schema):
    connect = postgres_schema
    with connect() as connection:
        first = run_atomic(
            connection,
            lambda cursor: append_audit_event(
                cursor, event_id="stable-operation-id", event_type="integration.test",
                entity_type="test", entity_id="first",
                occurred_at="2026-11-23T00:00:00Z", payload={"attempt": 1},
            ),
        )

    with connect() as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            run_atomic(
                connection,
                lambda cursor: append_audit_event(
                    cursor, event_id="stable-operation-id", event_type="integration.test",
                    entity_type="test", entity_id="duplicate",
                    occurred_at="2026-11-23T00:00:01Z", payload={"attempt": 2},
                ),
            )

    with connect() as connection:
        rows = connection.execute(
            "SELECT sequence, event_id FROM audit_events ORDER BY sequence"
        ).fetchall()
        head = connection.execute(
            "SELECT sequence, event_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()
    assert rows == [(1, "stable-operation-id")]
    assert head == (1, first["event_hash"])
