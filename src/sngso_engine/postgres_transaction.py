"""Small, explicit transaction boundary for PostgreSQL registry mutations.

The callback receives only a cursor, not the connection, so it cannot accidentally
commit or open a second transaction through the normal API. Callers must pass an
idle psycopg 3 connection. Exceptions are deliberately allowed to escape so the
transaction context rolls back the complete unit of work.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, Any

T = TypeVar("T")


class PostgresTransactionError(RuntimeError):
    """Raised when a PostgreSQL unit of work cannot establish its boundary."""


def run_atomic(
    connection: Any,
    operation: Callable[[Any], T],
) -> T:
    """Run one callback in one top-level READ COMMITTED transaction.

    The connection must be a psycopg 3 connection in IDLE state. Nested
    transactions are rejected: psycopg would otherwise create a savepoint,
    which would not provide the independent commit boundary required by
    registry state + audit-chain mutations.

    Do not retry this function automatically after a connection failure during
    commit. The commit outcome may be ambiguous; callers must reconnect and
    reconcile using durable operation/event identity before deciding whether
    to retry.
    """
    info = getattr(connection, "info", None)
    status = getattr(info, "transaction_status", None)
    if getattr(status, "name", None) != "IDLE":
        raise PostgresTransactionError(
            "PostgreSQL registry operations require an idle connection; "
            "nested or already-open transactions are not accepted"
        )

    transaction = getattr(connection, "transaction", None)
    cursor_factory = getattr(connection, "cursor", None)
    if not callable(transaction) or not callable(cursor_factory):
        raise TypeError("connection must provide psycopg transaction() and cursor()")

    with transaction():
        with cursor_factory() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            return operation(cursor)
