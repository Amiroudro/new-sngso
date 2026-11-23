from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sngso_engine.postgres_transaction import (
    PostgresTransactionError,
    run_atomic,
)


class FakeCursor:
    def __init__(self, calls: list[tuple]):
        self.calls = calls

    def __enter__(self):
        self.calls.append(("cursor_enter",))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.calls.append(("cursor_exit", exc_type))
        return False

    def execute(self, sql: str):
        self.calls.append(("execute", sql))


class FakeConnection:
    def __init__(self, status: str = "IDLE"):
        self.calls: list[tuple] = []
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name=status)
        )

    @contextmanager
    def transaction(self):
        self.calls.append(("transaction_enter",))
        try:
            yield
        except BaseException as exc:
            self.calls.append(("transaction_rollback", type(exc)))
            raise
        else:
            self.calls.append(("transaction_commit",))

    def cursor(self):
        return FakeCursor(self.calls)


def test_run_atomic_executes_callback_inside_one_transaction():
    connection = FakeConnection()

    result = run_atomic(
        connection,
        lambda cursor: (cursor.execute("INSERT INTO example VALUES (1)"), "ok")[1],
    )

    assert result == "ok"
    assert connection.calls == [
        ("transaction_enter",),
        ("cursor_enter",),
        ("execute", "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"),
        ("execute", "INSERT INTO example VALUES (1)"),
        ("cursor_exit", None),
        ("transaction_commit",),
    ]


def test_run_atomic_rolls_back_when_callback_fails():
    connection = FakeConnection()

    def fail(cursor):
        cursor.execute("INSERT INTO example VALUES (1)")
        raise ValueError("injected failure")

    with pytest.raises(ValueError, match="injected failure"):
        run_atomic(connection, fail)

    assert ("transaction_rollback", ValueError) in connection.calls
    assert not any(call[0] == "transaction_commit" for call in connection.calls)


@pytest.mark.parametrize("status", ["INTRANS", "INERROR", "ACTIVE", "UNKNOWN"])
def test_run_atomic_rejects_non_idle_connection_before_opening_transaction(status):
    connection = FakeConnection(status)

    with pytest.raises(PostgresTransactionError, match="idle connection"):
        run_atomic(connection, lambda cursor: None)

    assert connection.calls == []


def test_run_atomic_rejects_non_psycopg_connection_shape():
    with pytest.raises(PostgresTransactionError):
        run_atomic(SimpleNamespace(info=SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS")
        )), lambda cursor: None)
