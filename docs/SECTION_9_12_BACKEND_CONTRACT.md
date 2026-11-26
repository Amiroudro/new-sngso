# Worker Lease Backend Contract

## Purpose

The worker execution layer now depends on the public `WorkerLeaseBackend`
protocol rather than requiring the concrete SQLite store for managed heartbeat
sessions. This creates an explicit substitution boundary for future storage
backends without claiming that any backend has passed distributed-systems
acceptance merely by implementing the Python methods.

The current `WorkerLeaseStore` remains the only implementation and remains
SQLite-backed. Checkpoint recovery and managed heartbeat sessions now consume the protocol rather than inspecting the concrete store's SQLite registry attribute. The protocol is an API contract, not an implementation or a
database abstraction for the rest of the registry.

## Required operations

An implementation must provide:

- `acquire`: atomically establish one current owner per pipeline run, issue a
  cryptographically random one-time token, persist only its verifier, and
  allocate a strictly increasing fencing generation.
- `heartbeat`: verify token, owner, generation, active status, and expiry in
  the same transaction as the renewal and append-only revision.
- `assert_current`: verify the same ownership conditions against the
  authoritative store.
- `release`: atomically end current ownership and append its revision.
- `request_cancellation`: authorize at point of use and durably record an
  immutable request.
- `cancellation_requested`: return cancellation state only to the current
  authenticated lease holder.
- `acknowledge_cancellation`: atomically update pipeline cancellation and
  lease state, with audit evidence.
- `history`: return ordered immutable lease revisions.

## Transaction and consistency requirements

A production shared backend must demonstrate all of the following using
independent database connections and independent worker processes:

1. A uniqueness or equivalent serialization mechanism prevents two current
   owners for the same pipeline run.
2. Claim, expiry transition, generation increment, lease-head update, revision
   append, and audit event commit atomically.
3. Heartbeat and release use compare-and-swap or row-lock semantics so stale
   revisions cannot overwrite a newer owner.
4. A takeover increments the generation monotonically even after process
   crashes and database connection loss.
5. The authoritative check and protected state mutation cannot be separated
   by a race that lets a stale owner commit.
6. Database errors and ambiguous commit outcomes fail closed. The client must
   reconcile authoritative state before assuming acquisition or renewal failed
   or succeeded.
7. Cancellation intent and acknowledgement remain durable across worker
   takeover.
8. The backend's documented isolation, durability, failover, and clock
   assumptions are tested against the actual deployment configuration.

A fencing generation is useful only when downstream side-effect providers
atomically enforce it. A database lease alone cannot stop a paused process or
revoke an already-issued remote request.

## PostgreSQL implementation gate

PostgreSQL is a plausible shared transactional backend, but this repository
does not yet include a PostgreSQL lease implementation, PostgreSQL schema
migrations, connection-pool lifecycle, integration fixtures, or a CI service
that runs the lease conformance suite against PostgreSQL. No PostgreSQL
dependency or unverified adapter is added in this change.

Before implementation can be accepted, provision a disposable PostgreSQL
service in CI and run the same backend contract tests against it, including
multi-process contention, transaction aborts, connection drops around commit,
restart recovery, and failover behavior supported by the target deployment.
Do not substitute SQLite tests or mocks for these checks.

## Current status

- `WorkerLeaseBackend`: public runtime-checkable protocol.
- `WorkerLeaseStore`: current SQLite implementation, tested for independent
  connections sharing one local database file.
- PostgreSQL and other multi-host backends: not implemented.
- Distributed lease durability/HA: not accepted.


## Shared transaction boundary: a critical architectural constraint

A lease-only PostgreSQL database is not a valid replacement for the current
SQLite-backed registry.

Today, cancellation acknowledgement updates the worker lease, pipeline-run
status, and audit chain in one SQLite transaction. Checkpoint creation and
transitions likewise validate pipeline-run state and append audit events in
the same registry transaction. The audit chain is globally ordered within
that registry. Splitting leases into PostgreSQL while leaving runs, checkpoints,
and audit records in SQLite would turn these atomic operations into distributed
transactions that this engine does not implement.

Therefore, a production PostgreSQL path must choose one of these designs:

1. **Move the complete operational consistency boundary together.** Port the
   pipeline-run lifecycle, worker leases, stage checkpoints, and audit-chain
   writes to one PostgreSQL database and one transaction manager. Preserve
   uniqueness, revision compare-and-swap, append-only protections, audit-chain
   serialization, and migration compatibility. Other registry domains can
   remain SQLite only if they are outside these operational transactions and
   their cross-domain references are explicitly redesigned.
2. **Keep SQLite as the authoritative operational store.** Continue using it
   for leases, pipeline runs, checkpoints, and audit writes, accepting the
   same-host coordination boundary. Do not claim multi-host worker support.

A dual-write or outbox design may be considered only after specifying recovery,
ordering, idempotency, and consistency semantics. It must not be presented as
equivalent to the current single-transaction guarantees without proof.

The protocol currently makes the lease API substitutable for consumers such
as managed heartbeat sessions and checkpoint recovery. It does not make the
whole registry backend substitutable. A complete PostgreSQL migration is a
larger storage-engine project, not a small adapter.


## Complete registry migration design

The preferred PostgreSQL target is now specified in
[the operational PostgreSQL migration architecture](SECTION_9_OPERATIONAL_POSTGRESQL_MIGRATION_DESIGN.md).
The design clarifies that the existing audit chain is global across ordinary
source, permission, editorial, benchmark, staging, and operational registry
writes. Consequently, preserving the current single-chain semantics requires
moving the registry and its audit-producing writes together, not merely moving
the run/lease/checkpoint tables. A narrower boundary would require an explicit
audit-model redesign and separate acceptance.

The design is intentionally non-implementing. It defines the PostgreSQL schema
mapping, row-lock and compare-and-swap protocol, serialized audit-head update,
ambiguous-commit handling, schema-v35 data migration, cutover/rollback boundary,
backup/failover requirements, and real-PostgreSQL CI acceptance matrix. SQLite
remains the only implemented backend.
