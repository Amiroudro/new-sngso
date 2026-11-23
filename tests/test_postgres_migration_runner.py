from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sngso_engine.postgres_migration_runner import (
    PostgresMigrationError,
    apply_postgres_migrations,
)


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None, **kwargs):
        self.statements.append((sql, params, kwargs))
        if sql.lstrip().startswith("INSERT INTO postgres_schema_migrations"):
            self.rows.append(params)

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows=()):
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="IDLE")
        )
        self.db_cursor = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        self.info.transaction_status.name = "INTRANS"
        try:
            yield
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
        finally:
            self.info.transaction_status.name = "IDLE"

    def cursor(self):
        return self.db_cursor


def test_postgres_migration_runner_applies_and_records_audit_schema_once():
    connection = FakeConnection()
    applied = apply_postgres_migrations(connection)

    assert applied == (1, 2, 3)
    assert connection.commits == 1
    assert connection.rollbacks == 0
    sql = [statement[0] for statement in connection.db_cursor.statements]
    assert "SELECT pg_advisory_xact_lock(%s)" in sql
    migration_sql = next(statement for statement in sql if "CREATE TABLE audit_events" in statement)
    guard_sql = next(statement for statement in sql if "guard_audit_event_insert" in statement)
    registry_sql = next(statement for statement in sql if "CREATE TABLE sources" in statement)
    assert "CREATE TABLE audit_chain_head" in migration_sql
    assert "audit_events_no_update" in migration_sql
    assert "audit_events_no_delete" in migration_sql
    assert "audit_chain_head_update_guard" in guard_sql
    assert "audit_chain_head_no_delete" in guard_sql
    assert "CREATE TABLE permission_grants" in registry_sql
    assert "CREATE TABLE grant_endpoints" in registry_sql
    assert "CREATE TABLE domain_record_revisions" in registry_sql
    assert "ON DELETE RESTRICT" in registry_sql
    assert "domain_record_revisions_no_update" in registry_sql
    assert connection.db_cursor.rows[0][0:2] == (1, "0001_audit_chain.sql")
    assert connection.db_cursor.rows[1][0:2] == (2, "0002_audit_head_guards.sql")
    assert connection.db_cursor.rows[2][0:2] == (3, "0003_source_permission_domain_registry.sql")
    assert all(row[2].startswith("sha256:") for row in connection.db_cursor.rows)

    applied_again = apply_postgres_migrations(connection)
    assert applied_again == ()
    assert connection.commits == 2
    assert len(connection.db_cursor.rows) == 3


def test_postgres_migration_runner_rejects_modified_applied_migration():
    connection = FakeConnection(rows=[
        (1, "0001_audit_chain.sql", "sha256:" + "0" * 64)
    ])
    with pytest.raises(PostgresMigrationError, match="checksum differs"):
        apply_postgres_migrations(connection)
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_postgres_migration_runner_rejects_unknown_future_schema_version():
    connection = FakeConnection(rows=[
        (99, "future.sql", "sha256:" + "a" * 64)
    ])
    with pytest.raises(PostgresMigrationError, match="unsupported"):
        apply_postgres_migrations(connection)
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_postgres_migration_runner_rejects_noncontiguous_migration_ledger(monkeypatch):
    import sngso_engine.postgres_migration_runner as migration_runner

    monkeypatch.setattr(
        migration_runner,
        "_MIGRATIONS",
        (
            (1, "0001_audit_chain.sql"),
            (2, "0002_registry_core.sql"),
            (3, "0003_staging.sql"),
        ),
    )
    connection = FakeConnection(rows=[
        (1, "0001_audit_chain.sql", "sha256:" + "1" * 64),
        (3, "0003_staging.sql", "sha256:" + "3" * 64),
    ])
    with pytest.raises(PostgresMigrationError, match="not a contiguous prefix"):
        migration_runner.apply_postgres_migrations(connection)
    assert connection.rollbacks == 1
    assert connection.commits == 0
