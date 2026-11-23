from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql

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
            assert apply_postgres_migrations(connection) == (1,)
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
