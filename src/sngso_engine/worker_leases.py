"""Durable local worker leases, fencing generations, heartbeats, and cancellation."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from threading import Event, RLock, Thread
from datetime import datetime, timedelta, timezone
from typing import Any

from .canonical import sha256_fingerprint


class WorkerLeaseError(RuntimeError):
    """Base class for worker lease lifecycle failures."""


class LeaseConflictError(WorkerLeaseError):
    """A different live worker currently owns the run lease."""


class LeaseLostError(WorkerLeaseError):
    """The supplied token or fencing generation no longer owns the lease."""


class LeaseExpiredError(LeaseLostError):
    """The lease expired and cannot be renewed or used."""


class CancellationRequested(WorkerLeaseError):
    """Raised only by explicit worker code that chooses to stop on cancellation."""


@dataclass(frozen=True)
class WorkerLease:
    pipeline_run_id: str
    worker_id: str
    fencing_token: int
    token: str = field(repr=False)
    status: str = "active"
    acquired_at: str = ""
    heartbeat_at: str = ""
    expires_at: str = ""
    revision: int = 1
    cancellation_requested: bool = False


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("lease timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value):
        raise ValueError(f"{label} must be a bounded identifier")
    return value


def _reason_code(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", value):
        raise ValueError("reason_code must be a lowercase bounded identifier")
    return value


def _ttl(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 10 <= value <= 3600:
        raise ValueError("lease ttl_seconds must be an integer from 10 to 3600")
    return value


def _row_to_lease(row: Any, token: str) -> WorkerLease:
    return WorkerLease(
        pipeline_run_id=row["pipeline_run_id"],
        worker_id=row["worker_id"],
        fencing_token=row["fencing_token"],
        token=token,
        status=row["status"],
        acquired_at=row["acquired_at"],
        heartbeat_at=row["heartbeat_at"],
        expires_at=row["expires_at"],
        revision=row["revision"],
        cancellation_requested=row["cancel_requested_at"] is not None,
    )


class WorkerLeaseStore:
    """SQLite-backed lease manager.

    Tokens are returned once and only their SHA-256 fingerprints are persisted.
    A monotonically increasing fencing token distinguishes successive owners.
    Lease expiry is a takeover condition, not proof that an old process stopped.
    External side-effect adapters must enforce fencing at their own boundary.
    """

    def __init__(self, registry: Any) -> None:
        if not hasattr(registry, "_transaction") or not hasattr(registry, "_append_audit"):
            raise TypeError("registry must provide transactional SQLite registry operations")
        self.registry = registry

    @staticmethod
    def _token_hash(token: str) -> str:
        return sha256_fingerprint(token)

    def acquire(
        self, pipeline_run_id: str, worker_id: str, *, ttl_seconds: int = 60,
        actor: str, reason: str, now: datetime | None = None,
    ) -> WorkerLease:
        worker_id = _identity(worker_id, "worker_id")
        actor = _identity(actor, "actor")
        reason = _reason_code(reason)
        ttl = _ttl(ttl_seconds)
        current = _utc(now)
        stamp = _stamp(current)
        expires = _stamp(current + timedelta(seconds=ttl))
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        with self.registry._transaction() as connection:
            run = connection.execute(
                "SELECT status FROM pipeline_runs WHERE id = ?", (pipeline_run_id,)
            ).fetchone()
            if run is None:
                raise WorkerLeaseError("unknown pipeline run")
            if run["status"] != "running":
                raise WorkerLeaseError("worker lease can only be acquired for a running pipeline")
            row = connection.execute(
                "SELECT * FROM pipeline_worker_leases WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ).fetchone()
            if row is not None and row["status"] == "active":
                if _parse(row["expires_at"]) > current:
                    raise LeaseConflictError("another worker holds an unexpired lease")
                self._transition(
                    connection, row, status="expired", actor=actor,
                    reason="lease_expired_on_takeover", updated_at=stamp,
                )
                row = connection.execute(
                    "SELECT * FROM pipeline_worker_leases WHERE pipeline_run_id = ?",
                    (pipeline_run_id,),
                ).fetchone()
            if row is not None and row["status"] == "cancelled":
                raise WorkerLeaseError("a cancelled lease cannot be reacquired")
            if row is None:
                connection.execute(
                    """INSERT INTO pipeline_worker_leases
                       (pipeline_run_id, worker_id, token_hash, fencing_token, status,
                        acquired_at, heartbeat_at, expires_at, revision, updated_at, actor, reason)
                       VALUES (?, ?, ?, 1, 'active', ?, ?, ?, 1, ?, ?, ?)""",
                    (pipeline_run_id, worker_id, token_hash, stamp, stamp, expires,
                     stamp, actor, reason),
                )
                generation, revision = 1, 1
            else:
                generation = row["fencing_token"] + 1
                revision = row["revision"] + 1
                connection.execute(
                    """UPDATE pipeline_worker_leases SET worker_id = ?, token_hash = ?,
                       fencing_token = ?, status = 'active', acquired_at = ?, heartbeat_at = ?,
                       expires_at = ?, revision = ?, updated_at = ?, actor = ?, reason = ?
                       WHERE pipeline_run_id = ? AND revision = ?""",
                    (worker_id, token_hash, generation, stamp, stamp, expires, revision,
                     stamp, actor, reason, pipeline_run_id, row["revision"]),
                )
            self.registry._append_audit(
                connection, event_type="pipeline_worker_lease.acquired",
                entity_type="pipeline_run", entity_id=pipeline_run_id,
                payload={"worker_id": worker_id, "fencing_token": generation,
                         "expires_at": expires, "revision": revision,
                         "actor": actor, "reason": reason},
            )
            result = connection.execute(
                "SELECT * FROM pipeline_worker_leases WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ).fetchone()
        return _row_to_lease(result, token)

    def _owned_row(self, connection: Any, lease: WorkerLease, current: datetime) -> Any:
        if not isinstance(lease, WorkerLease):
            raise TypeError("lease must be a WorkerLease returned by acquire")
        row = connection.execute(
            "SELECT * FROM pipeline_worker_leases WHERE pipeline_run_id = ?",
            (lease.pipeline_run_id,),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise LeaseLostError("worker lease is no longer active")
        if row["worker_id"] != lease.worker_id or row["fencing_token"] != lease.fencing_token:
            raise LeaseLostError("worker lease ownership or fencing generation changed")
        if not secrets.compare_digest(row["token_hash"], self._token_hash(lease.token)):
            raise LeaseLostError("worker lease token is invalid")
        if _parse(row["expires_at"]) <= current:
            raise LeaseExpiredError("worker lease expired")
        return row

    def _transition(
        self, connection: Any, row: Any, *, status: str, actor: str,
        reason: str, updated_at: str, heartbeat_at: str | None = None,
        expires_at: str | None = None, cancel_requested_at: str | None = None,
        cancel_requested_by: str | None = None, cancel_reason_code: str | None = None,
    ) -> None:
        revision = row["revision"] + 1
        connection.execute(
            """UPDATE pipeline_worker_leases SET status = ?, heartbeat_at = ?,
               expires_at = ?, cancel_requested_at = ?, cancel_requested_by = ?,
               cancel_reason_code = ?, revision = ?, updated_at = ?, actor = ?, reason = ?
               WHERE pipeline_run_id = ? AND revision = ?""",
            (status, heartbeat_at or row["heartbeat_at"], expires_at or row["expires_at"],
             cancel_requested_at if cancel_requested_at is not None else row["cancel_requested_at"],
             cancel_requested_by if cancel_requested_by is not None else row["cancel_requested_by"],
             cancel_reason_code if cancel_reason_code is not None else row["cancel_reason_code"],
             revision, updated_at, actor, reason, row["pipeline_run_id"], row["revision"]),
        )
        self.registry._append_audit(
            connection, event_type=f"pipeline_worker_lease.{status}",
            entity_type="pipeline_run", entity_id=row["pipeline_run_id"],
            payload={"worker_id": row["worker_id"], "fencing_token": row["fencing_token"],
                     "revision": revision, "actor": actor, "reason": reason},
        )

    def heartbeat(
        self, lease: WorkerLease, *, ttl_seconds: int = 60, actor: str,
        reason: str = "heartbeat", now: datetime | None = None,
    ) -> WorkerLease:
        actor = _identity(actor, "actor")
        reason = _reason_code(reason)
        ttl = _ttl(ttl_seconds)
        current = _utc(now)
        stamp = _stamp(current)
        expires = _stamp(current + timedelta(seconds=ttl))
        with self.registry._transaction() as connection:
            row = self._owned_row(connection, lease, current)
            self._transition(
                connection, row, status="active", actor=actor, reason=reason,
                updated_at=stamp, heartbeat_at=stamp, expires_at=expires,
            )
            updated = connection.execute(
                "SELECT * FROM pipeline_worker_leases WHERE pipeline_run_id = ?",
                (lease.pipeline_run_id,),
            ).fetchone()
        return _row_to_lease(updated, lease.token)

    def assert_current(self, lease: WorkerLease, *, now: datetime | None = None) -> None:
        current = _utc(now)
        with self.registry._transaction("DEFERRED") as connection:
            self._owned_row(connection, lease, current)

    def release(
        self, lease: WorkerLease, *, actor: str, reason: str = "worker_finished",
        now: datetime | None = None,
    ) -> None:
        actor = _identity(actor, "actor")
        reason = _reason_code(reason)
        current = _utc(now)
        stamp = _stamp(current)
        with self.registry._transaction() as connection:
            row = self._owned_row(connection, lease, current)
            self._transition(
                connection, row, status="released", actor=actor,
                reason=reason, updated_at=stamp,
            )

    def request_cancellation(
        self, pipeline_run_id: str, *, actor: str, reason_code: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = _identity(actor, "actor")
        reason_code = _reason_code(reason_code)
        current = _utc(now)
        stamp = _stamp(current)
        with self.registry._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_worker_leases WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ).fetchone()
            if row is None or row["status"] != "active":
                raise WorkerLeaseError("cancellation requires an active worker lease")
            if _parse(row["expires_at"]) <= current:
                raise LeaseExpiredError("cannot request cancellation through an expired lease")
            if row["cancel_requested_at"] is not None:
                return {"requested": True, "already_requested": True,
                        "requested_at": row["cancel_requested_at"],
                        "reason_code": row["cancel_reason_code"]}
            self._transition(
                connection, row, status="active", actor=actor,
                reason="cancellation_requested", updated_at=stamp,
                cancel_requested_at=stamp, cancel_requested_by=actor,
                cancel_reason_code=reason_code,
            )
            self.registry._append_audit(
                connection, event_type="pipeline_run.cancellation_requested",
                entity_type="pipeline_run", entity_id=pipeline_run_id,
                payload={"requested_by": actor, "reason_code": reason_code,
                         "fencing_token": row["fencing_token"]},
            )
        return {"requested": True, "already_requested": False,
                "requested_at": stamp, "reason_code": reason_code}

    def cancellation_requested(
        self, lease: WorkerLease, *, now: datetime | None = None,
    ) -> bool:
        current = _utc(now)
        with self.registry._transaction("DEFERRED") as connection:
            row = self._owned_row(connection, lease, current)
            return row["cancel_requested_at"] is not None

    def acknowledge_cancellation(
        self, lease: WorkerLease, *, actor: str, now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = _identity(actor, "actor")
        current = _utc(now)
        stamp = _stamp(current)
        with self.registry._transaction() as connection:
            row = self._owned_row(connection, lease, current)
            if row["cancel_requested_at"] is None:
                raise WorkerLeaseError("no cancellation request is pending")
            run_row = connection.execute(
                "SELECT revision, payload_json FROM pipeline_runs WHERE id = ?",
                (lease.pipeline_run_id,),
            ).fetchone()
            if run_row is None:
                raise WorkerLeaseError("pipeline run no longer exists")
            import json
            record = json.loads(run_row["payload_json"])
            if record["status"] != "running":
                raise WorkerLeaseError("only a running pipeline can acknowledge cancellation")
            record.update(status="cancelled", finished_at=stamp)
            cancelled = self.registry.transition_pipeline_run(
                lease.pipeline_run_id, record,
                expected_revision=run_row["revision"], actor=actor,
                reason="worker_acknowledged_cancellation",
            )
            self._transition(
                connection, row, status="cancelled", actor=actor,
                reason="cancellation_acknowledged", updated_at=stamp,
            )
        return {"status": "cancelled", "pipeline_run_id": lease.pipeline_run_id,
                "pipeline_revision": cancelled["revision"], "fencing_token": lease.fencing_token}

    def history(self, pipeline_run_id: str) -> list[dict[str, Any]]:
        with self.registry._lock:
            rows = self.registry._connection.execute(
                """SELECT revision, created_at, worker_id, fencing_token, status,
                          acquired_at, heartbeat_at, expires_at, cancel_requested_at,
                          cancel_requested_by, cancel_reason_code, actor, reason
                   FROM pipeline_worker_lease_revisions
                   WHERE pipeline_run_id = ? ORDER BY revision""",
                (pipeline_run_id,),
            ).fetchall()
        return [dict(row) for row in rows]


class WorkerLeaseSession:
    """Optional in-process heartbeat loop for one worker lease.

    The session keeps the lease alive while caller code runs, exposes a
    cancellation event for cooperative polling, and never attempts to restart
    a lease after heartbeat failure.
    """

    def __init__(
        self, store: WorkerLeaseStore, lease: WorkerLease, *,
        heartbeat_interval_seconds: float = 10.0, ttl_seconds: int = 60,
    ) -> None:
        if not isinstance(store, WorkerLeaseStore) or not isinstance(lease, WorkerLease):
            raise TypeError("store and lease must be WorkerLeaseStore and WorkerLease")
        if isinstance(heartbeat_interval_seconds, bool) or not isinstance(
            heartbeat_interval_seconds, (int, float)
        ):
            raise ValueError("heartbeat interval must be a positive number")
        if heartbeat_interval_seconds <= 0 or heartbeat_interval_seconds * 2 >= _ttl(ttl_seconds):
            raise ValueError("heartbeat interval must be less than half the lease TTL")
        self.store = store
        self._lease = lease
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.ttl_seconds = _ttl(ttl_seconds)
        self._stop = Event()
        self.cancellation_event = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._error: Exception | None = None

    @property
    def lease(self) -> WorkerLease:
        with self._lock:
            return self._lease

    @property
    def heartbeat_error(self) -> Exception | None:
        with self._lock:
            return self._error

    def start(self) -> WorkerLeaseSession:
        if self._thread is not None:
            raise WorkerLeaseError("worker lease session has already been started")
        self.store.assert_current(self.lease)
        self._thread = Thread(
            target=self._heartbeat_loop,
            name=f"lease-heartbeat-{self.lease.pipeline_run_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            try:
                renewed = self.store.heartbeat(
                    self.lease, ttl_seconds=self.ttl_seconds,
                    actor=self.lease.worker_id, reason="heartbeat",
                )
                with self._lock:
                    self._lease = renewed
                if self.store.cancellation_requested(renewed):
                    self.cancellation_event.set()
            except Exception as exc:
                with self._lock:
                    self._error = exc
                self.cancellation_event.set()
                self._stop.set()
                return

    def check_cancellation(self) -> bool:
        self.assert_healthy()
        requested = self.store.cancellation_requested(self.lease)
        if requested:
            self.cancellation_event.set()
        return requested

    def assert_healthy(self) -> None:
        error = self.heartbeat_error
        if error is not None:
            raise LeaseLostError("worker heartbeat supervisor lost its lease") from error
        self.store.assert_current(self.lease)

    def close(self, *, release: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.heartbeat_interval_seconds * 2))
            if self._thread.is_alive():
                raise WorkerLeaseError("heartbeat thread did not stop within its join bound")
        if release and self.heartbeat_error is None:
            self.store.release(
                self.lease, actor=self.lease.worker_id, reason="worker_session_closed",
            )

    def __enter__(self) -> WorkerLeaseSession:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close(release=self.heartbeat_error is None)
