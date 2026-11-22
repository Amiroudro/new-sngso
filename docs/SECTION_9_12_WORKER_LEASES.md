# Section 9.12: Worker Leases, Heartbeats, and Cooperative Cancellation

Status: **local implementation candidate; full-suite CI acceptance pending.**

## Scope

Migration 33 adds a durable lease head and append-only revision history for each pipeline run. A lease is separate from the pipeline run's lifecycle state and from stage checkpoints.

The lease record contains only bounded operational metadata:

- pipeline run ID and worker identifier;
- a SHA-256 fingerprint of a one-time bearer token (the raw token is returned once and never stored);
- monotonically increasing fencing generation;
- active, released, expired, or cancelled state;
- acquisition, heartbeat, and expiry timestamps;
- an optional immutable cancellation request (requester and bounded reason code);
- revision, actor, and reason metadata.

## Ownership and expiry

1. A lease can be acquired only for a pipeline run already in `running` state.
2. One active, unexpired lease exists per run. SQLite's immediate write transaction serializes competing claimants.
3. A worker must present the one-time token and exact fencing generation to heartbeat, release, inspect cancellation, or acknowledge cancellation. Worker heartbeat, release, and acknowledgement audit actors must match the lease's worker ID. The cancellation requester is separately authorized at point of use.
4. Heartbeats extend expiry without changing the fencing generation.
5. An expired lease cannot be renewed or released by its former owner.
6. A new owner may take over only after the previous lease has expired or been explicitly released. The generation increments and a new token is issued.
7. Old tokens and generations are rejected after takeover.
8. Lease transitions and their append-only snapshots are committed with audit-chain events in the same transaction.

Expiry is a coordination signal, not proof that the old process has stopped. A paused or partitioned process may wake after its lease expires. Every side-effecting provider adapter must therefore enforce the fencing generation at the provider boundary, or use its own idempotency/reconciliation contract. The local lease manager alone cannot terminate a process or revoke an already-issued network request.

## Heartbeat and cancellation

- Heartbeats are explicit calls; the library does not start timers or background threads.
- TTL is bounded to 10–3600 seconds.
- Cancellation is a durable, immutable request attached to the current lease. Repeated requests return the existing request. The caller must supply an authorization callback that returns exactly `True`; exceptions and all other results deny the request.
- Workers poll cancellation between stages. The checkpoint-aware pipeline runner checks lease ownership before each stage, immediately before the handler, after the handler before checkpoint success, and before pipeline completion.
- Cancellation is cooperative. An in-flight handler must implement its own bounded timeout and cancellation-aware I/O.
- A worker acknowledges cancellation only while it still owns an unexpired lease. Acknowledgement atomically transitions the pipeline run to `cancelled` and the lease to `cancelled`.
- If a worker disappears, the request remains recorded and is inherited by a replacement lease so the replacement worker can observe and acknowledge it.

## API

`WorkerLeaseStore.acquire(...)` returns a `WorkerLease` containing the one-time token and fencing generation. The token is excluded from its representation. Store the token only in process memory or a protected secret handoff; never put it in logs, checkpoints, audit payloads, or pipeline records.

`heartbeat`, `assert_current`, `release`, `request_cancellation`, `cancellation_requested`, and `acknowledge_cancellation` expose the lifecycle operations. `WorkerLeaseSession` optionally runs a bounded in-process heartbeat thread, exposes a cancellation event, reports heartbeat failure, exposes `check_cancellation()` / `raise_if_cancelled()` for worker loops, and releases the lease when its context closes normally. It starts no thread until explicitly entered or started. `execute_checkpointed_pipeline` accepts an optional `lease_store` + `lease` pair to enforce lease checks and cooperative cancellation between stages. Existing runner behavior remains unchanged when these arguments are omitted.

## Verification requirements

- Migration from schema v32 to v33.
- Concurrent two-worker acquisition permits one winner.
- Heartbeat preserves generation and appends a revision.
- Managed heartbeat session renews while active, propagates cancellation, and releases on normal close.
- Expiry prevents renewal/release and permits a new owner with a higher generation.
- Old owner token/generation is fenced out after takeover.
- Raw bearer token is never persisted.
- Cancellation requests are idempotent and immutable.
- Cancellation before work prevents handler invocation.
- Cancellation requested during one stage prevents the next stage.
- Loss of lease during a handler prevents local success checkpointing; recovery must reconcile the provider operation before any replay.
- Append-only revision tampering is rejected.

## Remaining production gates

This is a local SQLite coordination primitive, not a distributed lock service or process supervisor. The heartbeat session is an in-process helper, not an external watchdog: it cannot recover a dead process or terminate a stuck handler. Production acceptance still requires provider-enforced fencing, authenticated worker identity, shared-database durability/HA, an external process supervisor, bounded stage timeouts, signal handling, and operational monitoring. No scheduler activation, live collection, publication, release, or deployment is authorized.
